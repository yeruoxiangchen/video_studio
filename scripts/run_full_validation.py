"""Run the complete bounded local validation, retaining each independent stage result."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from scripts.localization_eval_metrics import evaluation_protocol, suite_summary
from scripts.public_benchmark_protocol import SOURCE, BenchmarkRun, sha256, write_json
from scripts.run_public_holdout import application_snapshot


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def core_generation_environment(origin: str, health: dict[str, Any], key: str) -> dict[str, str]:
    if not health.get("visual"):
        raise ValueError("Core structured-generation model identity is unavailable")
    return {
        "CAMCAT_LLM_BASE_URL": origin,
        "CAMCAT_LLM_MODEL": str(health["visual"]),
        "CAMCAT_LLM_API_KEY": key,
    }


def junit_result(path: Path) -> dict[str, Any]:
    suites = list(ET.parse(path).getroot().iter("testsuite"))
    counts = {
        k: sum(int(s.attrib.get(k, 0)) for s in suites)
        for k in ("tests", "failures", "errors", "skipped")
    }
    return {
        **counts,
        "passed": counts["tests"] > 0
        and not any(counts[k] for k in ("failures", "errors", "skipped")),
    }


def reliability_result(data: Path, *, expected_pid: int, exit_code: int) -> dict[str, Any]:
    marker = read_json(data / "checks/reliability/latest-soak.json")
    output = marker.get("output")
    detail = read_json(Path(output) / "report.json") if output else {}
    summary = detail.get("summary", {})
    passed = (
        exit_code == 0
        and marker.get("pid") == detail.get("pid") == expected_pid
        and bool(marker.get("run_id"))
        and marker.get("run_id") == detail.get("run_id")
        and marker.get("mode") == detail.get("mode") == "soak"
        and marker.get("state") == detail.get("state") == "completed"
        and summary.get("completed_pipelines", 0) > 0
        and summary.get("failed_pipelines", 1) == 0
        and detail.get("health_samples", 0) > 0
        and not detail.get("cleanup_pending_jobs")
        and not detail.get("cleanup_pending_sessions")
    )
    return {
        "passed": passed,
        "output": output,
        "summary": summary,
        "evidence": str(Path(output) / "report.json") if output else None,
    }


def status(data: Path) -> dict[str, Any]:
    report = read_json(data / "benchmarks/runs/full_validation/latest.json")
    view = {
        k: report[k]
        for k in (
            "run_id",
            "pid",
            "state",
            "phase",
            "elapsed_seconds",
            "updated_at",
            "child_pid",
            "output",
            "summary",
            "error",
        )
        if k in report
    }
    children = {
        "public_localization": data / "benchmarks/runs/localization_public/latest.json",
        "business_regression": data / "checks/small_eval/latest.json",
        "stability_4_clients_10_minutes": data / "checks/reliability/latest-soak.json",
    }
    child = read_json(children[report["phase"]]) if report.get("phase") in children else {}
    if child.get("pid") == report.get("child_pid"):
        view["progress"] = {
            k: child[k]
            for k in (
                "phase",
                "sample_id",
                "completed_samples",
                "sample_count",
                "video_completed",
                "job_progress",
                "completed_batches",
                "health_samples",
                "summary",
                "quality",
            )
            if k in child
        }
    view["stages"] = [
        {k: s[k] for k in ("name", "passed", "exit_code", "seconds", "error") if k in s}
        for s in report.get("stages", [])
    ]
    for stage in view["stages"]:
        if (
            report.get("state") == "running"
            and stage["name"] == report.get("phase")
            and "exit_code" not in stage
            and "error" not in stage
        ):
            stage.pop("passed", None)
            stage["state"] = "running"
    if report.get("output"):
        findings = read_json(Path(report["output"]) / "review-findings.json")
        if findings:
            view["review_findings"] = findings
    return view or {"state": "not_started"}


def main() -> None:
    data = Path(os.environ["CAMCAT_STUDIO_DATA"])
    config_path = evaluation_protocol(SOURCE)
    config = read_json(config_path)
    run = BenchmarkRun(data / "benchmarks", "full_validation", config_path=config_path)
    stages: list[dict[str, Any]] = []
    run.report["stages"] = stages
    current: subprocess.Popen | None = None

    def stop(number: int, _frame: Any) -> None:
        if current and current.poll() is None:
            current.terminate()
            try:
                current.wait(timeout=45)
            except subprocess.TimeoutExpired:
                current.kill()
        run.save(state="cancelled", phase="cancelled")
        raise SystemExit(128 + number)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def execute(
        name: str,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        log_name: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        nonlocal current
        row: dict[str, Any] = {"name": name, "passed": False}
        stages.append(row)
        log = run.output / (log_name or (name + ".log"))
        began = time.monotonic()
        with log.open("w") as output:
            current = subprocess.Popen(
                args,
                cwd=SOURCE,
                env=env or os.environ.copy(),
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            run.save(phase=name, child_pid=current.pid, child_log=str(log))
            saved = 0.0
            while current.poll() is None:
                if time.monotonic() - saved > 15:
                    run.save(phase=name, child_elapsed_seconds=round(time.monotonic() - began, 1))
                    saved = time.monotonic()
                time.sleep(2)
            pid = current.pid
            row.update(
                exit_code=current.returncode,
                seconds=round(time.monotonic() - began, 3),
                log=str(log),
            )
            row["passed"] = current.returncode == 0
            current = None
        return row, pid

    try:
        # Existing public runs remain independent evidence, including the failed assembly task.
        public = read_json(data / "benchmarks/runs/editing_holdout/latest.json")
        if public.get("state") == "running":
            raise RuntimeError("Finish the current public editing run before starting this suite")
        run.report["existing_public_editing"] = {
            "run_id": public.get("run_id"),
            "summary": public.get("summary"),
            "report": public.get("output"),
            "scope": "Previously completed frozen-image public cases; reused without rerunning",
        }
        accepted = read_json(
            Path(
                config.get(
                    "source_receipt",
                    str(data / "checks/quality_completion_20261114/final-deployment-receipt.json"),
                )
            )
        )
        run.report["evaluation_scope"] = config.get("evaluation_scope", "frozen public subset")
        write_json(run.output / "selected-protocol.json", config)
        for name, expected in accepted["application_source_sha256"].items():
            if sha256(SOURCE / name) != expected:
                raise RuntimeError(
                    "Host application source differs from the accepted image: " + name
                )
        before = application_snapshot(data, config["application_image"])
        write_json(run.output / "application-before.json", before)
        for name in (
            "eval/localization_public_pilot_v1.json",
            "requirements.evaluation.txt",
            "scripts/run_full_validation.py",
            "scripts/run_localization_public_eval.py",
            "scripts/localization_eval_metrics.py",
            "scripts/prepare_localization_public.py",
        ):
            target = run.output / "source-snapshot" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((SOURCE / name).read_bytes())
        prep, _ = execute(
            "prepare_public_audio",
            [
                str(data / "local-models-venv/bin/python"),
                "-B",
                "-m",
                "scripts.prepare_localization_public",
            ],
        )
        if not prep["passed"]:
            raise RuntimeError("Public audio preparation failed; see stage log")
        inputs = read_json(data / "benchmarks/localization_public_v1/prepared-inputs.json")
        env = os.environ.copy()
        env.update(
            CAMCAT_EXTERNAL_CHINESE_AUDIO=inputs["samples"][0]["audio_path"],
            CAMCAT_NATIVE_PLANNING_BASE_URL="http://127.0.0.1:18974",
        )

        row, pid = execute(
            "public_localization",
            [
                str(data / "localization-eval-venv/bin/python"),
                "-B",
                "-u",
                "-m",
                "scripts.run_localization_public_eval",
            ],
            env=env,
        )
        result = read_json(data / "benchmarks/runs/localization_public/latest.json")
        row["passed"] &= (
            result.get("pid") == pid
            and result.get("state") == "completed"
            and result.get("automated_pass") is True
        )
        row["output"] = result.get("output")
        run.report["public_localization_quality"] = result.get("quality")
        run.report["manual_review"] = result.get("manual_review")

        contracts = [
            "tests/integration/test_migrations_real.py",
            "tests/integration/test_external_qwen.py",
            "tests/integration/test_local_models_real.py::test_invalid_visual_input_is_rejected_instead_of_embedding_null_text",
            "tests/integration/test_planning_model_real.py",
            "tests/integration/test_multilingual_asr_real.py",
            "tests/integration/test_translation_real.py",
            "tests/integration/test_localization_media_real.py",
            "tests/integration/test_localization_jobs_real.py",
            "tests/integration/test_concurrency_real.py",
        ]
        xml = run.output / "contracts.xml"
        row, _ = execute(
            "real_contracts_and_exceptions",
            [
                "bash",
                "scripts/studio.sh",
                "integration-local",
                *contracts,
                "--basetemp",
                str(run.output / "contract-temp"),
                "--junitxml",
                str(xml),
            ],
            env=env,
        )
        if xml.exists():
            row.update(junit_result(xml))
            row["passed"] &= row["exit_code"] == 0
        else:
            row.update(passed=False, error="Real contract test report was not produced")
        # The bounded generation extension belongs to the core model service.
        xml = run.output / "core-deadline.xml"
        program = (
            "import os,sys,httpx;"
            "from scripts.run_localization_public_eval import configure_host;configure_host();"
            "from scripts.run_full_validation import core_generation_environment;"
            "os.environ['CAMCAT_LOCAL_MODELS_EXTERNAL']='1';"
            "origin=os.environ['CAMCAT_EMBEDDING_BASE_URL'];"
            "health=httpx.get(origin+'/health',trust_env=False,timeout=10).json();"
            "os.environ.update(core_generation_environment(origin,health,os.environ['CAMCAT_EMBEDDING_API_KEY']));"
            "os.execv(sys.executable,[sys.executable,'-B','-m','pytest','-q','--tb=short',"
            "'tests/integration/test_local_models_real.py::test_expired_generation_releases_the_model_for_the_next_real_request',"
            "'--basetemp',"
            + repr(str(run.output / "core-deadline-temp"))
            + ",'--junitxml',"
            + repr(str(xml))
            + "])"
        )
        row, _ = execute(
            "core_generation_timeout_recovery",
            [str(data / "venv/bin/python"), "-B", "-c", program],
            env=env,
        )
        if xml.exists():
            row.update(junit_result(xml))
            row["passed"] &= row["exit_code"] == 0
        else:
            row.update(passed=False, error="Core deadline test report was not produced")

        row, pid = execute(
            "business_regression", ["bash", "scripts/studio.sh", "small-eval"], env=env
        )
        small = read_json(data / "checks/small_eval/latest.json")
        row["passed"] &= (
            small.get("pid") == pid
            and small.get("state") == "completed"
            and all(
                g["passed"] == g["total"] and not g["failed"] and not g["not_run"]
                for g in small.get("summary", {}).values()
            )
        )
        row["output"] = small.get("output")
        run.report["business_summary"] = small.get("summary")

        browser_env = {
            **env,
            "CAMCAT_E2E_VIDEO": str(data / "runtime/acceptance/source.mp4"),
            "CAMCAT_E2E_IMAGE": str(data / "runtime/acceptance/reference.jpg"),
            "CAMCAT_E2E_GOAL_SECONDS": "4",
            "CAMCAT_E2E_OUTPUT_DIR": str(run.output / "browser-results"),
            "CAMCAT_E2E_REPORT_DIR": str(run.output / "browser-report"),
        }
        row, _ = execute(
            "browser_journey",
            ["bash", "scripts/studio.sh", "browser", "camcat.spec.ts", "--reporter=json"],
            env=browser_env,
            log_name="browser.json",
        )
        if row["exit_code"] == 0:
            stats = read_json(run.output / "browser.json")["stats"]
            row["passed"] = stats["expected"] > 0 and not any(
                stats[k] for k in ("unexpected", "skipped", "flaky")
            )
            run.report["browser_stats"] = stats

        small_report = (
            read_json(Path(small["output"]) / "report.json") if small.get("output") else {}
        )
        source_job = small_report.get("input", {}).get("source_job_id")
        if source_job:
            row, pid = execute(
                "stability_4_clients_10_minutes",
                [
                    "bash",
                    "scripts/studio.sh",
                    "reliability",
                    "soak",
                    "--source-job",
                    source_job,
                    "--clients",
                    "4",
                    "--duration",
                    "600",
                    "--interval",
                    "240",
                ],
                env=env,
            )
            stability = reliability_result(data, expected_pid=pid, exit_code=row["exit_code"])
            row.update({k: stability[k] for k in ("passed", "output", "evidence")})
            run.report["stability_summary"] = stability["summary"]
        else:
            stages.append(
                {
                    "name": "stability_4_clients_10_minutes",
                    "passed": False,
                    "error": "Fresh analyzed source unavailable from business regression",
                }
            )
        after = application_snapshot(data, config["application_image"])
        write_json(run.output / "application-after.json", after)
        stages.append({"name": "frozen_application_identity", "passed": after == before})
        if config.get("repair_editing_result"):
            repair = read_json(Path(config["repair_editing_result"]))
            deadline = time.monotonic() + 1800
            while repair.get("state") == "running" and time.monotonic() < deadline:
                run.save(phase="await_public_editing", public_editing_pid=repair.get("pid"))
                time.sleep(10)
                repair = read_json(Path(config["repair_editing_result"]))
            run.report["repair_public_editing"] = repair
            public = (
                repair
                if (
                    repair.get("state") == "completed"
                    and repair.get("application_image") == config["application_image"]
                    and repair.get("application_unchanged") is True
                )
                else {}
            )
            stages.append(
                {
                    "name": "public_editing_regression_execution",
                    "passed": bool(public)
                    and public.get("summary", {}).get("execution_errors") == 0,
                }
            )
        run.report["summary"] = suite_summary(stages, public.get("summary", {}))
        run.save(state="completed", phase="completed", manual_review_pending=True)
    except Exception as error:
        run.save(state="failed", phase="failed", error=str(error)[:500])
        raise


if __name__ == "__main__":
    data = Path(os.environ.get("CAMCAT_STUDIO_DATA", "/data/zjr/camcat_studio"))
    if sys.argv[1:] == ["--status"]:
        print(json.dumps(status(data), ensure_ascii=False, indent=2))
    else:
        main()
