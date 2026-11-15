"""Run frozen delivery evaluations in order, stopping on the first failed stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def public_delivery_passed(report: dict[str, Any], expected_ids: set[str]) -> bool:
    rows = report.get("cases", [])
    summary = report.get("summary", {})
    return (
        report.get("state") == "completed"
        and len(rows) == len(expected_ids) == 2
        and {row.get("id") for row in rows} == expected_ids
        and summary.get("tasks") == summary.get("executed") == summary.get("full_reward") == 2
        and summary.get("execution_errors") == 0
        and all(
            row.get("state") == "executed"
            and row.get("reward") == 1
            and "harness_error" not in row
            and row.get("verifier_calibration") == {"oracle": 1, "missing_output": 0}
            for row in rows
        )
    )


def load_status(root: Path, *, public_first: bool) -> dict[str, Any]:
    folder = "delivery_validation" if public_first else "edit_regressions"
    latest = root / "checks" / folder / "latest.json"
    if not latest.exists():
        return {"state": "not_started"}
    status = json.loads(latest.read_text())
    paths = {
        "public_editing": root / "benchmarks/runs/editing/latest.json",
        "small_eval": root / "checks/small_eval/latest.json",
    }
    child = paths.get(status.get("phase"))
    if child and child.exists():
        try:
            progress = json.loads(child.read_text())
        except json.JSONDecodeError:
            return status
        if progress.get("pid") == status.get("child_pid"):
            status["progress"] = {
                key: progress[key]
                for key in ("phase", "elapsed_seconds", "job_progress", "active_jobs", "summary")
                if key in progress
            }
            status["progress"]["cases"] = [
                {
                    key: row[key]
                    for key in ("id", "state", "status", "seconds", "reward", "error")
                    if key in row
                }
                for row in progress.get("cases", [])
            ]
    return status


def main(*, public_first: bool = False) -> None:
    source = Path(__file__).resolve().parents[1]
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    folder = "delivery_validation" if public_first else "edit_regressions"
    output = root / "checks" / folder / run_id
    output.mkdir(parents=True)
    status: dict[str, Any] = {
        "run_id": run_id,
        "pid": os.getpid(),
        "output": str(output),
        "state": "running",
        "automatic_restart": False,
        "baseline": str(root / "checks/small_eval/20261113T001557Z-c1aebf/report.json"),
        "repair_source_sha256": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in (
                "apps/api/camcat/agent/graph.py",
                "apps/api/camcat/agent/context.py",
                "apps/api/camcat/agent/sequence.py",
                "apps/api/camcat/editing/policies.py",
                "apps/api/camcat/editing/requirements.py",
                "apps/api/camcat/domain/render_policy.py",
                "apps/api/camcat/rendering/policy.py",
                "apps/api/camcat/domain/commands.py",
                "apps/api/camcat/domain/reducer.py",
                "apps/api/camcat/api.py",
                "apps/api/camcat/worker.py",
                "apps/api/camcat/repositories.py",
                "apps/api/camcat/timeline/compiler.py",
                "apps/api/camcat/services/providers.py",
                "apps/api/camcat/media/ffmpeg.py",
                "apps/web/e2e/camcat.spec.ts",
                "services/local_models/budget.py",
                "services/local_models/server.py",
                "services/local_models/runtime.py",
                "eval/public_pilot_v1.json",
                "eval/small_tasks_v1.json",
                "scripts/run_public_editing.py",
                "scripts/public_benchmark_protocol.py",
                "scripts/public_bench.sh",
                "scripts/run_small_eval.py",
                "scripts/small_eval_checks.py",
                "scripts/validate_edit_regressions.py",
                "scripts/studio.sh",
            )
        },
    }

    def save(phase: str) -> None:
        status.update(phase=phase, updated_at=datetime.now(UTC).isoformat())
        encoded = json.dumps(status, ensure_ascii=False, indent=2) + "\n"
        (output / "status.json").write_text(encoded)
        pending = output.parent / "latest.tmp"
        pending.write_text(encoded)
        pending.replace(output.parent / "latest.json")
        print(encoded, flush=True)

    def terminate(signum: int, _frame: Any) -> None:
        status["state"] = "cancelled"
        save("cancelled")
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    def run(arguments: list[str], log_name: str, environment: dict[str, str]) -> int:
        with (
            (output / log_name).open("w") as log,
            subprocess.Popen(
                ["bash", "scripts/studio.sh", *arguments],
                cwd=source,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            ) as process,
        ):
            status["child_pid"] = process.pid
            save(status["phase"])
            return process.wait()

    try:
        if public_first:
            for name in status["repair_source_sha256"]:
                destination = output / "source-snapshot" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                content = (source / name).read_bytes()
                if hashlib.sha256(content).hexdigest() != status["repair_source_sha256"][name]:
                    raise RuntimeError(f"Source changed while freezing the run: {name}")
                destination.write_bytes(content)
            previous = json.loads((root / "benchmarks/runs/editing/latest.json").read_text())
            previous_report = Path(previous["output"]) / "report.json"
            status["public_baseline"] = {
                "report": str(previous_report),
                "sha256": hashlib.sha256(previous_report.read_bytes()).hexdigest(),
            }
            save("public_editing")
            code = run(["public-bench", "editing"], "public-editing.log", os.environ.copy())
            if code:
                raise RuntimeError(f"Public editing process exited {code}; see public-editing.log")
            public = json.loads((root / "benchmarks/runs/editing/latest.json").read_text())
            if public["pid"] != status["child_pid"]:
                raise RuntimeError(
                    "Another public evaluation replaced latest.json; see public-editing.log"
                )
            status.update(
                public_editing_output=public["output"], public_summary=public.get("summary")
            )
            config = json.loads((source / "eval/public_pilot_v1.json").read_text())
            expected_ids = {task["id"] for task in config["agentic"]["tasks"]}
            if not public_delivery_passed(public, expected_ids):
                raise RuntimeError(
                    "Public editing delivery or scoring failed; original results preserved"
                )
        save("small_eval")
        code = run(["small-eval"], "small-eval.log", os.environ.copy())
        if code:
            raise RuntimeError(f"small-eval process exited {code}; see small-eval.log")
        evaluation = json.loads((root / "checks/small_eval/latest.json").read_text())
        if evaluation["pid"] != status["child_pid"]:
            raise RuntimeError(
                "Another evaluation replaced latest.json; see this run's small-eval.log"
            )
        status.update(small_eval_output=evaluation["output"], summary=evaluation["summary"])
        if evaluation["state"] != "completed" or any(
            group["failed"] or group["not_run"] for group in evaluation["summary"].values()
        ):
            raise RuntimeError("Six-case business evaluation failed; original results preserved")
        environment = os.environ.copy()
        environment.update(
            CAMCAT_E2E_VIDEO=str(root / "runtime/acceptance/source.mp4"),
            CAMCAT_E2E_IMAGE=str(root / "runtime/acceptance/reference.jpg"),
            CAMCAT_E2E_GOAL_SECONDS="4",
            CAMCAT_E2E_OUTPUT_DIR=str(output / "browser-results"),
            CAMCAT_E2E_REPORT_DIR=str(output / "browser-report"),
        )
        save("browser")
        code = run(["browser", "camcat.spec.ts", "--reporter=json"], "browser.json", environment)
        if code:
            raise RuntimeError(f"Browser regression exited {code}; see browser.json")
        browser = json.loads((output / "browser.json").read_text())
        status["browser_stats"] = browser["stats"]
        if (
            browser["stats"]["expected"] < 1
            or browser["stats"]["unexpected"]
            or browser["stats"]["skipped"]
        ):
            raise RuntimeError("Browser regression did not execute and pass the real journey")
        status["state"] = "completed"
        save("completed")
    except Exception as exc:
        status.update(state="failed", error=str(exc))
        save("failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-editing-first", action="store_true")
    parser.add_argument("--status", action="store_true")
    arguments = parser.parse_args()
    if arguments.status:
        view = load_status(
            Path(os.environ["CAMCAT_STUDIO_DATA"]), public_first=arguments.public_editing_first
        )
        # Keep the full source receipt on disk; watch should show live task progress.
        view = {
            key: value
            for key, value in view.items()
            if key not in {"repair_source_sha256", "baseline", "public_baseline"}
        }
        print(json.dumps(view, ensure_ascii=False, indent=2))
    else:
        main(public_first=arguments.public_editing_first)
