"""Recover verifier infrastructure without rerunning model or media-analysis tasks."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from scripts.public_benchmark_protocol import BenchmarkRun, sha256, verify_receipts, write_json
from scripts.run_public_editing import PublicEditing


def verification_paths(root: Path, kind: str, run_id: str) -> tuple[Path, Path]:
    if kind not in {"editing", "editing_holdout", "editing_repair"}:
        raise ValueError("Unsupported editing report namespace")
    base = root / "runs" / kind
    parent = base / run_id
    if parent.parent.resolve() != base.resolve() or parent.name != run_id:
        raise ValueError("Run must be a direct child of the selected report directory")
    manifest = (
        parent / "inputs.json"
        if kind == "editing_repair"
        else root / "holdout_v1/inputs.json"
        if kind == "editing_holdout"
        else root / "agentic-pilot.json"
    )
    return parent, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument(
        "--kind", choices=["editing", "editing_holdout", "editing_repair"], default="editing"
    )
    args = parser.parse_args()
    root = Path(os.environ["CAMCAT_STUDIO_DATA"]) / "benchmarks"
    parent, manifest_path = verification_paths(root, args.kind, args.run)
    original_path = parent / "report.json"
    original = json.loads(original_path.read_text())
    run = BenchmarkRun(root, "editing_validation")
    run.report.update(
        parent_run_id=args.run,
        parent_kind=args.kind,
        parent_output=str(parent),
        mode="verifier-only recovery; no model or source-analysis retries",
        cases=[],
    )
    checker = object.__new__(PublicEditing)
    checker.root = root
    manifest = json.loads(manifest_path.read_text())
    tasks = {task["id"]: task for task in manifest["tasks"]}
    verified = set()
    try:
        verify_receipts(root)
        if args.kind in {"editing_holdout", "editing_repair"}:
            verify_receipts(root, receipts_path=root / "holdout_v1/upstream-receipts.json")
        if original["input_manifest_sha256"] != sha256(manifest_path):
            raise ValueError("Inputs changed since the original run")
        write_json(run.output / "parent-report-observed.json", original)
        while True:
            original = json.loads(original_path.read_text())
            for case in original.get("cases", []):
                identifier = case["id"]
                if identifier in verified or case["state"] in {"running", "pending"}:
                    continue
                output = run.output / identifier
                output.mkdir()
                for filename in ("solution.json", "solution.mp4"):
                    artifact = parent / identifier / filename
                    if artifact.is_file():
                        (output / filename).symlink_to(artifact)
                row = {
                    "id": identifier,
                    "original_execution_state": case["state"],
                    "original_execution_error": case.get("error"),
                    "original_execution_error_type": case.get("error_type"),
                    "original_harness_error": case.get("harness_error"),
                }
                run.save(phase="verify:" + identifier)
                row["official_result"] = checker.score(tasks[identifier], output)
                row["calibration"] = checker.calibrate(tasks[identifier], output)
                run.report["cases"].append(row)
                verified.add(identifier)
                run.save(phase="verified:" + identifier)
            if original["state"] != "running":
                break
            if not args.follow:
                run.save(state="partial", phase="parent_still_running")
                return
            run.save(phase="waiting_for_original_run")
            try:
                os.kill(original["pid"], 0)
            except ProcessLookupError as error:
                raise RuntimeError(
                    "Original controller exited without a terminal report"
                ) from error
            time.sleep(15)
        if verified != set(tasks):
            raise RuntimeError("Original run ended without a terminal result for each task")
        rows = run.report["cases"]
        run.report["parent_report_sha256"] = sha256(original_path)
        write_json(run.output / "parent-report-final.json", original)
        run.save(
            state="completed",
            phase="completed",
            summary={
                "tasks": len(rows),
                "execution_errors": sum(r["original_execution_state"] == "error" for r in rows),
                "full_reward": sum(r["official_result"]["reward"] == 1 for r in rows),
                "mean_reward": sum(r["official_result"]["reward"] for r in rows) / len(rows),
                "verifier_calibrations_passed": len(rows),
                "model_attempts_added": 0,
            },
        )
    except Exception as error:
        run.save(state="failed", phase="failed", error=str(error)[:1000])
        raise


if __name__ == "__main__":
    main()
