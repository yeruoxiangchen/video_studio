"""Two public editing tasks through CamCat's real API and upstream video verifiers."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any

import httpx

from scripts.public_benchmark_protocol import (
    SOURCE,
    BenchmarkRun,
    manifest_from_saved_state,
    oracle_command,
    sha256,
    transport_agentic_brief,
    verify_receipts,
    write_json,
)
from scripts.run_small_eval import safe


class PublicEditing:
    def __init__(self, root: Path, run: BenchmarkRun) -> None:
        self.root, self.run = root, run
        self.client = httpx.AsyncClient(
            base_url="http://127.0.0.1:18960", timeout=60, trust_env=False
        )
        self.jobs: set[str] = set()
        self.graph_runs: set[str] = set()

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        started = time.monotonic()
        response = await self.client.request(method, path, **kwargs)
        with (self.run.output / "api-calls.jsonl").open("a") as output:
            output.write(
                json.dumps(
                    {
                        "method": method,
                        "path": path,
                        "status": response.status_code,
                        "seconds": round(time.monotonic() - started, 4),
                        "request_id": response.headers.get("x-request-id"),
                    }
                )
                + "\n"
            )
        if not response.is_success:
            raise RuntimeError(
                f"{method} {path} HTTP {response.status_code}: {safe(response.text)[:600]}"
            )
        return response.json() if response.content else {}

    async def wait_job(self, identifier: str, phase: str) -> dict[str, Any]:
        self.jobs.add(identifier)
        last_saved = 0.0
        while True:
            job = await self.request("GET", "/api/v1/jobs/" + identifier)
            if job["status"] in {"succeeded", "failed", "cancelled", "dead_letter"}:
                self.jobs.discard(identifier)
                if job["status"] != "succeeded":
                    raise RuntimeError(
                        "Native job " + job["status"] + ": " + str(safe(job.get("error")))
                    )
                return job
            if time.monotonic() - last_saved > 15:
                self.run.save(
                    phase=phase, active_jobs=sorted(self.jobs), job_progress=job.get("progress")
                )
                last_saved = time.monotonic()
            await asyncio.sleep(2)

    async def cancel_pending_jobs(self) -> list[str]:
        requested = []
        for identifier in sorted(self.jobs):
            with suppress(Exception):
                await self.request("POST", "/api/v1/jobs/" + identifier + "/cancel")
                requested.append(identifier)
                self.jobs.discard(identifier)
        return requested

    async def execute(self, task: dict[str, Any], row: dict[str, Any]) -> None:
        output = self.run.output / task["id"]
        output.mkdir(parents=True)
        official = (
            self.root
            / "upstream/agentic-pinned/tasks"
            / ("agentic_vbench_" + task["family"])
            / task["id"]
        )
        original_brief = (official / "steps/solve/instruction.md").read_text()
        instruction, transport_notes = transport_agentic_brief(original_brief)
        (output / "original-instruction.md").write_text(original_brief)
        (output / "submitted-instruction.md").write_text(instruction)
        row.update(
            transport_notes=transport_notes,
            original_brief_sha256=sha256(output / "original-instruction.md"),
            submitted_brief_sha256=sha256(output / "submitted-instruction.md"),
        )
        media = sorted(Path(task["materials"]).glob("*.mp4"), key=lambda path: int(path.stem))
        expected = {item["file"]: item["sha256"] for item in task["media"]}
        if len(media) != task["clip_count"] or any(
            sha256(path) != expected[path.name] for path in media
        ):
            raise ValueError("Public task materials changed")
        self.run.save(phase=task["id"] + ":upload")
        with ExitStack() as files:
            multipart = [
                ("files", (path.name, files.enter_context(path.open("rb")), "video/mp4"))
                for path in media
            ]
            uploaded = await self.request(
                "POST", "/api/v1/source-media", files=multipart, data={"analysis_mode": "keyframes"}
            )
        row["source_job_id"] = uploaded["job_id"]
        row["source_expires_at"] = uploaded["expires_at"]
        source_names = {item["media_id"]: item["filename"] for item in uploaded["media"]}
        write_json(output / "upload.json", safe(uploaded))
        analyzed = await self.wait_job(uploaded["job_id"], task["id"] + ":source_analysis")
        write_json(output / "analysis.json", safe(analyzed))
        project = await self.request(
            "POST",
            "/api/v1/projects",
            json={"name": "Public pilot " + task["id"] + " " + self.run.report["run_id"]},
        )
        packet = await self.request(
            "POST",
            "/api/v1/editing/sessions",
            json={
                "project_id": project["project_id"],
                "source_job_id": uploaded["job_id"],
                "current_goal": "Public AgenticVBench pilot: " + task["id"],
            },
        )
        row.update(project_id=project["project_id"], session_id=packet["editing_session_id"])
        write_json(output / "before.json", safe(packet))
        self.run.save(phase=task["id"] + ":agent")
        completed = None
        event = ""
        nodes = []
        started = time.monotonic()
        async with self.client.stream(
            "POST",
            "/api/v1/editing/sessions/" + packet["editing_session_id"] + "/agent/stream",
            json={"base_version": packet["state_version"], "instruction": instruction, "top_k": 4},
            timeout=httpx.Timeout(1800, read=45),
        ) as response:
            if not response.is_success:
                await response.aread()
                raise RuntimeError(
                    f"Agent request HTTP {response.status_code}: {safe(response.text)[:600]}"
                )
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[5:])
                    if data.get("graph_run_id"):
                        row["graph_run_id"] = data["graph_run_id"]
                        self.graph_runs.add(data["graph_run_id"])
                    if event == "node_completed":
                        nodes.append({k: data.get(k) for k in ("node", "status", "duration_ms")})
                        row["nodes"] = nodes
                        self.run.save(phase=task["id"] + ":" + str(data.get("node")))
                    elif event == "failed":
                        row["agent_seconds"] = round(time.monotonic() - started, 3)
                        raise RuntimeError(str(safe(data.get("message"))))
                    elif event == "completed":
                        completed = data["session"]
        row["agent_seconds"] = round(time.monotonic() - started, 3)
        if completed is None:
            raise RuntimeError("Agent stream ended without a saved output")
        write_json(output / "after.json", safe(completed))
        submitted = await self.request(
            "POST",
            "/api/v1/editing/sessions/" + packet["editing_session_id"] + "/render",
            json={"base_version": completed["state_version"], "quality_profile": "standard"},
        )
        row["render_job_id"] = submitted["job_id"]
        rendered = await self.wait_job(submitted["job_id"], task["id"] + ":render")
        write_json(output / "render.json", safe(rendered))
        async with self.client.stream(
            "GET", rendered["result"]["download_url"], timeout=90
        ) as response:
            response.raise_for_status()
            with (output / "solution.mp4").open("wb") as stream:
                async for block in response.aiter_bytes():
                    stream.write(block)
        manifest = manifest_from_saved_state(completed["state"], source_names)
        write_json(output / "solution.json", manifest)
        row.update(
            saved_version=completed["state_version"],
            artifact=str(output / "solution.mp4"),
            manifest_segments=len(manifest["segments"]),
        )

    def score(self, task: dict[str, Any], output: Path, prefix: str = "") -> dict[str, Any]:
        official = (
            self.root
            / "upstream/agentic-pinned/tasks"
            / ("agentic_vbench_" + task["family"])
            / task["id"]
        )
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(official / "steps/solve/tests/judge.py"),
                "--solution",
                str(output / "solution.json"),
                "--solution-mp4",
                str(output / "solution.mp4"),
                "--materials-dir",
                task["materials"],
                "--reward-json",
                str(output / (prefix + "reward.json")),
                "--reward-txt",
                str(output / (prefix + "reward.txt")),
            ],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        (output / (prefix + "verifier.log")).write_text(result.stdout + "\n" + result.stderr)
        if result.returncode:
            raise RuntimeError("Official verifier failed: " + result.stderr[-600:])
        return json.loads((output / (prefix + "reward.json")).read_text())

    def calibrate(self, task: dict[str, Any], output: Path) -> dict[str, Any]:
        """The original oracle runs after the agent, in its own isolated filesystem view."""
        official = (
            self.root
            / "upstream/agentic-pinned/tasks"
            / ("agentic_vbench_" + task["family"])
            / task["id"]
        )
        oracle = output / "oracle-workspace"
        (oracle / "output").mkdir(parents=True)
        (oracle / "work").mkdir()
        (oracle / "materials").mkdir()
        command = oracle_command(
            oracle,
            Path(task["materials"]),
            official / "steps/solve/solution/solve.sh",
            Path(os.environ["CAMCAT_STUDIO_DATA"]) / "tools/ffmpeg",
        )
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        (output / "oracle.log").write_text(result.stdout + "\n" + result.stderr)
        if result.returncode:
            raise RuntimeError("Official oracle execution failed: " + result.stderr[-600:])
        positive = self.score(task, oracle / "output")
        missing = output / "missing-output-control"
        missing.mkdir()
        negative = self.score(task, missing)
        if positive.get("reward") != 1 or negative.get("reward") != 0:
            raise RuntimeError("Official verifier failed the oracle/missing-output calibration")
        return {"oracle": positive["reward"], "missing_output": negative["reward"]}

    async def run_all(self, tasks: list[dict[str, Any]], timeout: int) -> None:
        await self.request("GET", "/health/ready")
        rows = [
            {"id": t["id"], "family": t["family"], "state": "pending", "attempts": 1} for t in tasks
        ]
        self.run.report["cases"] = rows
        for task, row in zip(tasks, rows, strict=True):
            row["state"] = "running"
            started = time.monotonic()
            try:
                async with asyncio.timeout(timeout):
                    await self.execute(task, row)
                row["state"] = "executed"
            except Exception as error:
                row.update(
                    state="error",
                    error=(
                        "Task exceeded its configured wall-clock budget"
                        if isinstance(error, TimeoutError)
                        else str(safe(str(error)))[:1000]
                    ),
                    error_type=type(error).__name__,
                )
                row["cancel_requested_jobs"] = await self.cancel_pending_jobs()
            row["seconds"] = round(time.monotonic() - started, 3)
            output = self.run.output / task["id"]
            output.mkdir(exist_ok=True)
            try:
                reward = self.score(task, output)
                row["reward"] = reward["reward"]
                row["score_details"] = {
                    k: v
                    for k, v in reward["details"].items()
                    if k
                    in {
                        "reason",
                        "n_slots",
                        "n_correct",
                        "n_honest",
                        "n_honest_correct",
                        "pass_rate",
                        "honesty_factor",
                        "ordering",
                    }
                }
                row["verifier_calibration"] = self.calibrate(task, output)
            except Exception as error:
                row["harness_error"] = str(safe(str(error)))[:1000]
            self.run.save(phase="finished:" + task["id"], active_jobs=sorted(self.jobs))


async def main() -> None:
    root = Path(os.environ["CAMCAT_STUDIO_DATA"]) / "benchmarks"
    run = BenchmarkRun(root, "editing")
    runner = PublicEditing(root, run)
    try:
        verify_receipts(root)
        config = json.loads((SOURCE / "eval/public_pilot_v1.json").read_text())["agentic"]
        manifest = json.loads((root / "agentic-pilot.json").read_text())
        run.report.update(
            benchmark="AgenticVBench",
            scope=(
                "Two fixed public tasks, one attempt each; CamCat API adapter "
                "with unchanged official video verifier, not a full Harbor leaderboard run"
            ),
            task_count=len(manifest["tasks"]),
            source_revision=config["revision"],
            runner_sha256=sha256(Path(__file__)),
            input_manifest_sha256=sha256(root / "agentic-pilot.json"),
            external_model_api_spend=False,
        )
        await runner.run_all(manifest["tasks"], config["agent_timeout_seconds"])
        rows = run.report["cases"]
        valid = all("reward" in row and "harness_error" not in row for row in rows)
        run.save(
            state="completed" if valid else "failed",
            phase="completed" if valid else "harness_failed",
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
