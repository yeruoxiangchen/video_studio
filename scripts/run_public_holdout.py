"""Evaluate two new public inputs with the existing API adapter and original judges."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from scripts.prepare_public_benchmarks import fetch, hf_files, pin_upstream_files
from scripts.public_benchmark_protocol import (
    SOURCE,
    BenchmarkRun,
    sha256,
    verify_receipts,
    write_json,
)
from scripts.run_public_editing import PublicEditing
from scripts.run_small_eval import safe


def validate_protocol(config: dict[str, Any], pilot: dict[str, Any]) -> None:
    spec = config["agentic"]
    if (
        spec["repository"] != pilot["agentic"]["repository"]
        or spec["revision"] != pilot["agentic"]["revision"]
        or spec["task_attempts"] != 1
        or spec["agent_timeout_seconds"] != 1800
        or spec["verifier_timeout_seconds"] != 1800
    ):
        raise ValueError("Holdout must preserve the pinned source, one attempt and task budgets")
    tasks = spec["tasks"]
    previous = {t["id"] for t in pilot["agentic"]["tasks"]}
    if len(tasks) != 2 or {t["family"] for t in tasks} != {"assembly", "sequencing"}:
        raise ValueError("Require exactly one new task from each supported family")
    if previous.intersection(t["id"] for t in tasks):
        raise ValueError("Holdout cannot include a task from the pilot")
    for task in tasks:
        family = task["family"]
        number = 1
        while f"agentic-vbench-{family}-task{number}" in previous:
            number += 1
        if (
            task["id"] != f"agentic-vbench-{family}-task{number}"
            or task["archive"] != f"materials/{number}.zip"
            or task["dataset"] != f"ameddserM/agentic_vbench_video_{family}"
            or not isinstance(task["clip_count"], int)
            or task["clip_count"] <= 0
        ):
            raise ValueError("Task selection and numeric media scope must match the frozen rule")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", config["application_image"]):
        raise ValueError("Require an explicit deployed application image")


def application_snapshot(data: Path, expected_image: str) -> dict[str, Any]:
    environment = {
        **os.environ,
        "DOCKER_HOST": f"unix://{data}/docker/docker.sock",
        "DOCKER_CONFIG": str(data / "docker/client"),
    }
    docker = str(data / "tools/docker/docker")
    images = {}
    for role in ("api", "worker", "localization-worker", "render-worker"):
        images[role] = subprocess.check_output(
            [docker, "inspect", "--format", "{{.Image}}", f"camcat-studio-{role}-1"],
            env=environment,
            text=True,
            timeout=20,
        ).strip()
    if set(images.values()) != {expected_image}:
        raise ValueError("Deployed application differs from the frozen holdout image")
    names = [
        "CAMCAT_LLM_MODEL",
        "CAMCAT_LLM_BASE_URL",
        "CAMCAT_VISUAL_ANALYSIS_MODEL",
        "CAMCAT_VISUAL_ANALYSIS_BASE_URL",
        "CAMCAT_NARRATIVE_MODEL",
        "CAMCAT_NARRATIVE_BASE_URL",
        "CAMCAT_TRANSLATION_MODEL",
        "CAMCAT_TRANSLATION_BASE_URL",
        "CAMCAT_TRANSLATION_BACKEND",
    ]
    program = (
        "import json,os; print(json.dumps({k:os.environ.get(k) for k in " + repr(names) + "}))"
    )
    roles = json.loads(
        subprocess.check_output(
            [docker, "exec", "camcat-studio-api-1", "python", "-B", "-c", program],
            env=environment,
            text=True,
            timeout=20,
        )
    )
    providers = {}
    provider_names = [
        "local-models-server.json",
        "visual-model-server.json",
        "planning-model-server.json",
    ]
    if roles.get("CAMCAT_TRANSLATION_BACKEND") == "hymt":
        provider_names.append("specialist-translation-server.json")
    for name in provider_names:
        status = json.loads((data / "checks" / name).read_text())
        providers[name] = {
            k: status[k]
            for k in (
                "pid",
                "server_pid",
                "model",
                "revision",
                "source_sha256",
                "controller_sha256",
            )
            if k in status
        }
    return {"images": images, "generation_roles": roles, "loaded_provider_identity": providers}


def prepare_inputs(
    root: Path, config: dict[str, Any], pilot: dict[str, Any], run: BenchmarkRun
) -> Path:
    # This reuses pinned fetch/checksum code; it does not prepare or change the old pilot.
    run.save(phase="prepare:official_sources")
    receipts = pin_upstream_files({"agentic": config["agentic"], "msrvtt": pilot["msrvtt"]})
    receipt_path = root / "holdout_v1/upstream-receipts.json"
    write_json(receipt_path, receipts)
    verify_receipts(root, receipts_path=receipt_path)
    locks = json.loads((root / "agentic-data-lock.json").read_text())
    tasks = []
    for task in config["agentic"]["tasks"]:
        run.save(phase="prepare:" + task["id"])
        lock = locks[task["dataset"]]
        revision = lock["revision"]
        identity = hf_files(task["dataset"], revision, [task["archive"]])[task["archive"]]
        url = f"https://huggingface.co/datasets/{task['dataset']}/resolve/{revision}/{task['archive']}"
        folder = root / "data/agentic/holdout_v1" / task["family"]
        archive, materials = folder / "materials.zip", folder / "materials"
        fetch(url, archive, expected=identity["sha256"])
        materials.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            media = [
                m
                for m in package.infolist()
                if not m.is_dir() and re.fullmatch(r"\d+\.mp4", Path(m.filename).name)
            ]
            expected = {f"{i}.mp4" for i in range(1, task["clip_count"] + 1)}
            if {Path(m.filename).name for m in media} != expected or len(media) != len(expected):
                raise ValueError("Archive does not match the frozen candidate set")
            if sum(m.file_size for m in media) > 2 * 1024**3:
                raise ValueError("Selected public inputs exceed the small-evaluation size cap")
            for item in media:
                target = materials / Path(item.filename).name
                with package.open(item) as inp, target.open("wb") as out:
                    while block := inp.read(1024 * 1024):
                        out.write(block)
        tasks.append(
            {
                **task,
                "dataset_revision": revision,
                "dataset_card_license": lock.get("license"),
                "source_url": url,
                "archive_sha256": identity["sha256"],
                "materials": str(materials),
                "media": [
                    {"file": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)}
                    for p in sorted(materials.glob("*.mp4"))
                ],
            }
        )
    manifest = root / "holdout_v1/inputs.json"
    write_json(
        manifest,
        {
            "tasks": tasks,
            "source_revision": config["agentic"]["revision"],
            "scope": "Public benchmark inputs, transient uploads; no library ingestion",
        },
    )
    return manifest


async def main() -> None:
    data = Path(os.environ["CAMCAT_STUDIO_DATA"])
    root = data / "benchmarks"
    config_path = SOURCE / "eval/public_holdout_v1.json"
    run = BenchmarkRun(root, "editing_holdout", config_path=config_path)
    runner = PublicEditing(root, run)
    try:
        config = json.loads(config_path.read_text())
        pilot = json.loads((SOURCE / "eval/public_pilot_v1.json").read_text())
        validate_protocol(config, pilot)
        frozen = application_snapshot(data, config["application_image"])
        write_json(run.output / "application-before.json", frozen)
        run.report.update(
            benchmark="AgenticVBench",
            task_count=2,
            external_model_api_spend=False,
            evaluation_role="new tasks not used for pilot repairs at selection time",
            scope=(
                "Two new fixed public tasks via the existing CamCat API adapter and original judges"
            ),
            source_revision=config["agentic"]["revision"],
            application_image=config["application_image"],
            requested_task_ids=[t["id"] for t in config["agentic"]["tasks"]],
            base_adapter_sha256=sha256(SOURCE / "scripts/run_public_editing.py"),
            holdout_runner_sha256=sha256(Path(__file__)),
            excluded_pilot_config_sha256=sha256(SOURCE / "eval/public_pilot_v1.json"),
        )
        for name in (
            "eval/public_holdout_v1.json",
            "scripts/run_public_holdout.py",
            "scripts/run_public_editing.py",
            "scripts/public_benchmark_protocol.py",
        ):
            target = run.output / "source-snapshot" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((SOURCE / name).read_bytes())
        manifest_path = prepare_inputs(root, config, pilot, run)
        if sha256(config_path) != run.report["config_sha256"]:
            raise ValueError("Holdout protocol changed during input preparation")
        manifest = json.loads(manifest_path.read_text())
        run.report["input_manifest_sha256"] = sha256(manifest_path)
        await runner.run_all(manifest["tasks"], config["agentic"]["agent_timeout_seconds"])
        after = application_snapshot(data, config["application_image"])
        write_json(run.output / "application-after.json", after)
        if after != frozen:
            raise ValueError("Application or loaded model identity changed during evaluation")
        rows = run.report["cases"]
        valid = all("reward" in row and "harness_error" not in row for row in rows)
        run.save(
            state="completed" if valid else "failed",
            phase="completed" if valid else "harness_failed",
            application_unchanged=True,
            summary={
                "tasks": len(rows),
                "executed": sum(r["state"] == "executed" for r in rows),
                "execution_errors": sum(r["state"] == "error" for r in rows),
                "full_reward": sum(r.get("reward") == 1 for r in rows),
                "mean_reward": sum(r.get("reward", 0) for r in rows) / len(rows) if valid else None,
            },
        )
    except Exception as error:
        run.save(state="failed", phase="failed", error=str(safe(str(error)))[:1000])
        raise
    finally:
        run.report["cancel_requested_jobs"] = await runner.cancel_pending_jobs()
        run.report["remaining_jobs"] = sorted(runner.jobs)
        run.report["observed_graph_runs"] = sorted(runner.graph_runs)
        write_json(run.output / "report.json", run.report)
        write_json(run.latest, run.report)
        await runner.client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
