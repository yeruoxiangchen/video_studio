"""Run the real browser journey and report directly observed, correlated timings."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


def summarize_acceptance(packet: dict[str, Any]) -> dict[str, Any]:
    phases = {
        "source_upload_and_analysis": ("upload_start", "analysis_done"),
        "agent_planning": ("plan_start", "plan_done"),
        "translation_and_speech": ("localization_start", "localization_done"),
        "render_and_validation": ("render_start", "render_done"),
        "download": ("render_done", "downloaded"),
    }
    markers = packet.get("markers", [])
    times = {row["name"]: float(row["seconds"]) for row in markers}
    required = {name for pair in phases.values() for name in pair}
    if packet.get("state") != "passed" or not required.issubset(times):
        raise ValueError("a complete successful upload-to-download journey is required")
    ordered = [float(row["seconds"]) for row in markers]
    if ordered != sorted(ordered) or any(value < 0 for value in ordered):
        raise ValueError("browser markers must use a monotonic clock")
    jobs = packet.get("jobs", [])
    kinds = {job["kind"] for job in jobs}
    if not {"analyze_source", "translate_subtitles", "dub_subtitles", "render"}.issubset(kinds):
        raise ValueError("actual analysis, translation, speech and render jobs are required")
    if any(job["status"] != "succeeded" for job in jobs):
        raise ValueError("a job did not succeed")
    graph = packet.get("graph", {})
    if graph.get("status") != "succeeded":
        raise ValueError("the actual editing Agent run must succeed")
    analysis = next(job for job in jobs if job["kind"] == "analyze_source")
    render = next(job for job in jobs if job["kind"] == "render")
    verification = render.get("result", {})
    if analysis.get("result", {}).get("frame_backend") != "native":
        raise ValueError("source analysis did not use the native batch decoder")
    if verification.get("full_decode_succeeds") is not True or verification.get("frame_delta") != 0:
        raise ValueError("the actual rendered media did not pass complete decode/frame validation")
    lifetimes = []
    for job in jobs:
        created, started, finished = [
            datetime.fromisoformat(job[key].replace("Z", "+00:00"))
            for key in ("created_at", "started_at", "finished_at")
        ]
        if not created <= started <= finished:
            raise ValueError("job lifecycle timestamps are out of order")
        lifetimes.append(
            {
                "job_id": job["job_id"],
                "kind": job["kind"],
                "attempts": job["attempts"],
                "queue_seconds": round((started - created).total_seconds(), 3),
                "execution_seconds": round((finished - started).total_seconds(), 3),
                "total_seconds": round((finished - created).total_seconds(), 3),
            }
        )
    return {
        "state": "passed",
        "frame_backend": "native",
        "journeys": 1,
        "scope": "Warm services; actual browser upload, source analysis, editing Agent, "
        "subtitle timing review, translation, speech, delivery render, playback and download. "
        "Wall time includes automated UI interactions; no cross-run percentile is inferred.",
        "upload_to_download_seconds": round(times["downloaded"] - times["upload_start"], 3),
        "phases_seconds": {
            key: round(times[end] - times[start], 3) for key, (start, end) in phases.items()
        },
        "jobs": lifetimes,
        "agent_nodes": [
            {
                key: row[key]
                for key in ("node_name", "status", "duration_ms", "provider_calls")
                if key in row
            }
            for row in graph.get("node_trace", [])
        ],
        "render_verification": {
            key: verification.get(key)
            for key in (
                "duration_seconds",
                "width",
                "height",
                "actual_frames",
                "expected_frames",
                "frame_delta",
                "duration_delta_seconds",
                "full_decode_succeeds",
                "output_sha256",
                "encoder",
            )
        },
    }


def resource_sample(source: Path) -> dict[str, Any]:
    sample: dict[str, Any] = {"time": datetime.now(UTC).isoformat()}
    command = ["bash", "scripts/studio.sh", "compose", "stats", "--no-stream", "--format", "json"]
    try:
        result = subprocess.run(command, cwd=source, capture_output=True, text=True, timeout=15)
        rows = []
        for line in result.stdout.splitlines():
            packet = json.loads(line)
            rows.extend(packet if isinstance(packet, list) else [packet])
        sample["containers"] = [
            {key: row.get(key) for key in ("Name", "CPUPerc", "MemUsage", "MemPerc", "PIDs")}
            for row in rows
        ]
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        sample["shared_host_gpu_devices"] = result.stdout.strip().splitlines()
        sample["gpu_scope"] = "Device totals include other administrators' processes."
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        sample["sampling_error_type"] = type(error).__name__
    return sample


def cancel_owned_jobs(progress: Path, api: str) -> None:
    if not progress.is_file():
        return
    packet = json.loads(progress.read_text())
    with httpx.Client(base_url=api, trust_env=False, timeout=10) as client:
        ids = set(packet.get("job_ids", []))
        if packet.get("session_id"):
            response = client.get(f"/api/v1/editing/sessions/{packet['session_id']}/localization")
            if response.is_success:
                ids.update(row["job_id"] for row in response.json().get("jobs", []))
        for job_id in ids:
            response = client.get(f"/api/v1/jobs/{job_id}")
            if response.is_success and response.json().get("status") in {"queued", "running"}:
                client.post(f"/api/v1/jobs/{job_id}/cancel")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api", default="http://127.0.0.1:18960")
    parser.add_argument("--web", default="http://127.0.0.1:18962")
    args = parser.parse_args()

    def interrupted(_signal: int, _frame: Any) -> None:
        raise KeyboardInterrupt("pipeline measurement stopped")

    signal.signal(signal.SIGTERM, interrupted)
    if not args.video.is_file():
        parser.error("a local licensed video is required")
    source = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    progress = out / "progress.json"
    env = dict(
        os.environ,
        CAMCAT_PIPELINE_VIDEO=str(args.video.resolve()),
        CAMCAT_PIPELINE_PROGRESS=str(progress),
        CAMCAT_E2E_BASE_URL=args.web,
        CAMCAT_E2E_API_BASE=args.api,
        CAMCAT_E2E_OUTPUT_DIR=str(out / "results"),
        CAMCAT_E2E_REPORT_DIR=str(out / "browser-report"),
        PLAYWRIGHT_JSON_OUTPUT_NAME=str(out / "browser.json"),
    )
    command = [
        "node",
        "apps/web/node_modules/@playwright/test/cli.js",
        "test",
        "--config=apps/web/playwright.config.ts",
        "engineering-pipeline.spec.ts",
        "--workers=1",
        "--reporter=list,json",
    ]
    status = {
        "state": "running",
        "pid": os.getpid(),
        "output": str(out),
        "started_at": datetime.now(UTC).isoformat(),
        "automatic_restart": False,
    }
    samples = []
    process = None
    started = time.monotonic()
    try:
        with (out / "browser.log").open("w") as log:
            process = subprocess.Popen(
                command,
                cwd=source,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            while process.poll() is None:
                if time.monotonic() - started > 27 * 60:
                    raise TimeoutError("full browser journey exceeded its 27 minute budget")
                samples.append(resource_sample(source))
                status.update(
                    elapsed_seconds=round(time.monotonic() - started, 2),
                    child_pid=process.pid,
                    resource_samples=len(samples),
                )
                (out / "status.json").write_text(json.dumps(status, indent=2) + "\n")
                (out / "resources.json").write_text(json.dumps(samples, indent=2) + "\n")
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=12)
        if process.returncode:
            raise RuntimeError("browser journey failed; inspect browser.log and retained trace")
        acceptance = list((out / "results").glob("*/acceptance.json"))
        if len(acceptance) != 1:
            raise ValueError("exactly one real browser acceptance record is required")
        report = summarize_acceptance(json.loads(acceptance[0].read_text()))
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        status["state"] = "completed"
        return 0
    except BaseException as error:
        status.update(state="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        try:
            cancel_owned_jobs(progress, args.api)
        finally:
            status.update(
                elapsed_seconds=round(time.monotonic() - started, 2),
                updated_at=datetime.now(UTC).isoformat(),
            )
            (out / "status.json").write_text(
                json.dumps(status, ensure_ascii=False, indent=2) + "\n"
            )
            print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
