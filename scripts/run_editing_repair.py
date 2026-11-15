"""Real API regression on the previously inspected public editing cases."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from scripts.localization_eval_metrics import evaluation_protocol
from scripts.public_benchmark_protocol import SOURCE, BenchmarkRun, sha256, write_json
from scripts.run_public_editing import PublicEditing
from scripts.run_public_holdout import application_snapshot


async def main() -> None:
    data = Path(os.environ["CAMCAT_STUDIO_DATA"])
    config_path = evaluation_protocol(SOURCE)
    config = json.loads(config_path.read_text())
    root = data / "benchmarks"
    run = BenchmarkRun(root, "editing_repair", config_path=config_path)
    runner = PublicEditing(root, run)
    try:
        before = application_snapshot(data, config["application_image"])
        write_json(run.output / "application-before.json", before)
        old = json.loads((root / "agentic-pilot.json").read_text())["tasks"]
        newer = json.loads((root / "holdout_v1/inputs.json").read_text())["tasks"]
        # Run the failed long narrative first so a regression is found early.
        tasks = [
            t
            for group, family in (
                (old, "sequencing"),
                (newer, "sequencing"),
                (old, "assembly"),
                (newer, "assembly"),
            )
            for t in group
            if t["family"] == family
        ]
        run.report.update(
            evaluation_scope="Development regression on previously inspected public cases",
            task_count=len(tasks),
            application_image=config["application_image"],
        )
        write_json(run.output / "inputs.json", {"tasks": tasks})
        run.report["input_manifest_sha256"] = sha256(run.output / "inputs.json")
        await runner.run_all(tasks, 1800)
        after = application_snapshot(data, config["application_image"])
        write_json(run.output / "application-after.json", after)
        if before != after:
            raise ValueError("Application or model identity changed during the regression")
        rows = run.report["cases"]
        valid = all("reward" in row and "harness_error" not in row for row in rows)
        run.save(
            state="completed" if valid else "failed",
            phase="completed",
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
        run.save(
            state="failed", phase="failed", error=type(error).__name__ + ": " + str(error)[:300]
        )
        raise
    finally:
        run.report["cancel_requested_jobs"] = await runner.cancel_pending_jobs()
        run.report["remaining_jobs"] = sorted(runner.jobs)
        write_json(run.output / "report.json", run.report)
        write_json(run.latest, run.report)
        await runner.client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
