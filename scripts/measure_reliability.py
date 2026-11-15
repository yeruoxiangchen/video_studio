"""Bounded real-service performance and soak checks; artifacts stay on the data disk."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def utc() -> str:
    return datetime.now(UTC).isoformat()


class Measurement:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = Path(os.environ.get("CAMCAT_STUDIO_DATA", "/data/zjr/camcat_studio"))
        self.source = Path(__file__).resolve().parents[1]
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
        self.out = self.root / "checks/reliability" / (args.mode + "-" + self.run_id)
        self.out.mkdir(parents=True)
        self.started = time.monotonic()
        self.last_saved = self.started
        self.sessions: set[str] = set()
        self.active_jobs: set[str] = set()
        self.report: dict[str, Any] = {
            "state": "running",
            "mode": args.mode,
            "run_id": self.run_id,
            "started_at": utc(),
            "pid": os.getpid(),
            "output": str(self.out),
            "workload": {
                "video_seconds": 12,
                "width": 1280,
                "height": 720,
                "cues": 2,
                "fps": 30,
                "render_quality": "preview",
                "clients": args.clients,
                "duration_seconds": args.duration,
                "source_job_id": args.source_job,
                "scope": "Existing analyzed source; real translation, speech and render. "
                "Source analysis and Agent planning are excluded from timing.",
            },
            "http_load": [],
            "observed_jobs": {},
            "batches": [],
            "health_samples": 0,
            "resources": [],
        }
        self.client = httpx.AsyncClient(
            base_url=args.api,
            timeout=60,
            trust_env=False,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=20),
        )
        model_status = self.root / "checks/local-models-server.json"
        self.model_status = json.loads(model_status.read_text()) if model_status.exists() else {}
        self.report["existing_model_gpus"] = [
            {
                key: gpu.get(key)
                for key in ("index", "uuid", "role", "precision", "budget_mib", "memory_fraction")
            }
            for gpu in self.model_status.get("gpus", [])
        ]
        self.report["source_sha256"] = {
            name: hashlib.sha256((self.source / name).read_bytes()).hexdigest()
            for name in (
                "apps/api/camcat/api.py",
                "apps/api/camcat/repositories.py",
                "apps/web/nginx.conf",
                "scripts/measure_reliability.py",
            )
        }

    def save(self, phase: str, **extra: Any) -> None:
        self.last_saved = time.monotonic()
        self.report.update(
            phase=phase,
            updated_at=utc(),
            elapsed_seconds=round(time.monotonic() - self.started, 2),
            **extra,
        )
        temporary = self.out / "report.tmp"
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(self.out / "report.json")
        status = {
            key: self.report[key]
            for key in (
                "state",
                "mode",
                "run_id",
                "pid",
                "phase",
                "updated_at",
                "elapsed_seconds",
                "output",
            )
        }
        status["completed_batches"] = len(self.report["batches"])
        status["health_samples"] = self.report["health_samples"]
        status["active_jobs"] = sorted(self.active_jobs)
        status["job_progress"] = [
            job
            for job in self.report["observed_jobs"].values()
            if job["job_id"] in self.active_jobs
        ]
        if self.report.get("error"):
            status["error"] = self.report["error"]
        (self.out / "status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2) + "\n"
        )
        (self.out.parent / ("latest-" + self.args.mode + ".json")).write_text(
            json.dumps(status, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(status, ensure_ascii=False), flush=True)

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(
                f"{method} {path.split('?')[0]} returned HTTP {response.status_code}"
            )
        return response.json() if response.content else {}

    async def new_session(self, *, footage: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"project_id": self.project_id, "current_goal": "真实服务性能验收"}
        if footage:
            body["source_job_id"] = self.args.source_job
        packet = await self.request("POST", "/api/v1/editing/sessions", json=body)
        self.sessions.add(packet["editing_session_id"])
        return packet

    async def health(self) -> None:
        for address in (
            self.args.api + "/health/ready",
            self.args.web + "/health",
            "http://127.0.0.1:18969/health",
        ):
            response = await self.client.get(address, timeout=20)
            if response.status_code != 200:
                raise RuntimeError(f"Health check returned HTTP {response.status_code}")
        self.report["health_samples"] += 1

    async def resources(self) -> None:
        process = await asyncio.create_subprocess_exec(
            "bash",
            "scripts/studio.sh",
            "compose",
            "stats",
            "--no-stream",
            "--format",
            "json",
            cwd=self.source,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), 20)
        rows = []
        if process.returncode == 0:
            for line in stdout.decode().splitlines():
                packet = json.loads(line)
                rows.extend(packet if isinstance(packet, list) else [packet])
        resource: dict[str, Any] = {
            "time": utc(),
            "docker_stats_exit": process.returncode,
            "containers": [
                {key: row.get(key) for key in ("Name", "CPUPerc", "MemUsage", "MemPerc", "PIDs")}
                for row in rows
            ],
        }
        indices = sorted({str(gpu["index"]) for gpu in self.model_status.get("gpus", [])})
        if indices:
            process = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--id=" + ",".join(indices),
                "--query-gpu=index,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), 15)
            resource["gpu_samples"] = [
                dict(
                    zip(
                        ("index", "used_mib", "total_mib", "utilization_percent"),
                        [int(value.strip()) for value in line.split(",")],
                        strict=True,
                    )
                )
                for line in stdout.decode().splitlines()
            ]
        pid = self.model_status.get("pid")
        status_path = Path(f"/proc/{pid}/status")
        if status_path.exists():
            resource["model_process"] = {
                line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                for line in status_path.read_text().splitlines()
                if line.startswith(("VmRSS:", "Threads:"))
            }
        self.report["resources"].append(resource)

    async def read_load(self, session_id: str) -> None:
        paths = [
            "/api/v1/projects?include_sessions=true&limit=10",
            "/api/v1/jobs?limit=10",
            f"/api/v1/editing/sessions/{session_id}",
            f"/api/v1/editing/sessions/{session_id}/localization",
        ]
        for concurrency in (1, 4, 8, 16):
            self.save("http_load", concurrency=concurrency)
            semaphore = asyncio.Semaphore(concurrency)

            async def one(index: int, limiter: asyncio.Semaphore = semaphore) -> tuple[int, float]:
                async with limiter:
                    start = time.monotonic()
                    response = await self.client.get(paths[index % len(paths)])
                    return response.status_code, (time.monotonic() - start) * 1000

            began = time.monotonic()
            outcomes = await asyncio.gather(*(one(index) for index in range(160)))
            elapsed = time.monotonic() - began
            latencies = [latency for _, latency in outcomes]
            statuses = Counter(status for status, _ in outcomes)
            self.report["http_load"].append(
                {
                    "target": "direct API; four DB-backed read routes",
                    "concurrency": concurrency,
                    "requests": len(outcomes),
                    "seconds": round(elapsed, 3),
                    "requests_per_second": round(len(outcomes) / elapsed, 2),
                    "statuses": dict(statuses),
                    "mean_ms": round(statistics.mean(latencies), 3),
                    "p50_ms": percentile(latencies, 0.50),
                    "p95_ms": percentile(latencies, 0.95),
                    "p99_ms": percentile(latencies, 0.99),
                }
            )
            if statuses != {200: 160}:
                raise RuntimeError("DB-backed read load included unexpected HTTP responses")
        self.save("http_load_complete")

    async def wait_job(self, job_id: str, *, cancelled: bool = False) -> dict[str, Any]:
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            job = await self.request("GET", "/api/v1/jobs/" + job_id)
            self.report["observed_jobs"][job_id] = {
                key: job.get(key) for key in ("job_id", "kind", "status", "progress", "attempts")
            }
            if time.monotonic() - self.last_saved >= 10:
                self.save(self.report["phase"])
            if job["status"] in {"succeeded", "failed", "cancelled", "dead_letter"}:
                self.active_jobs.discard(job_id)
                expected = "cancelled" if cancelled else "succeeded"
                if job["status"] != expected:
                    raise RuntimeError(f"Job {job_id} ended {job['status']}, expected {expected}")
                return job
            await asyncio.sleep(0.5)
        raise RuntimeError(f"Job {job_id} exceeded the 360 second observation budget")

    async def submit(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        job = await self.request("POST", path, json=body)
        self.active_jobs.add(job["job_id"])
        self.save(self.report["phase"])
        return job

    @staticmethod
    def timing(job: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: job.get(key)
            for key in (
                "job_id",
                "kind",
                "status",
                "attempts",
                "created_at",
                "started_at",
                "finished_at",
            )
        }
        created = datetime.fromisoformat(job["created_at"])
        started = datetime.fromisoformat(job["started_at"])
        finished = datetime.fromisoformat(job["finished_at"])
        result.update(
            queue_seconds=(started - created).total_seconds(),
            execution_seconds=(finished - started).total_seconds(),
            total_seconds=(finished - created).total_seconds(),
        )
        for key in ("tts_api_calls", "cache_hits"):
            if key in (job.get("result") or {}):
                result[key] = job["result"][key]
        return result

    async def pipeline(self, index: int, batch_number: int) -> dict[str, Any]:
        began = time.monotonic()
        session = await self.new_session(footage=True)
        session_id = session["editing_session_id"]
        path = "/api/v1/editing/sessions/" + session_id
        source_id = next(
            source["source_id"]
            for source in session["state"]["sources"]
            if source["origin"] == "user_upload"
        )
        cues = [
            f"这是第{index + 1}段视频，欢迎一起分享地球的故事。",
            "字幕翻译与英文配音，让更多人理解同一个世界。",
        ]
        await self.request(
            "POST",
            path + "/commands",
            json={
                "base_version": 1,
                "reason": "Authored 12-second performance workload",
                "commands": [
                    {
                        "type": "update_canvas",
                        "canvas": {"width": 1280, "height": 720, "fps_num": 30, "fps_den": 1},
                    },
                    {
                        "type": "add_track",
                        "track": {
                            "type": "video",
                            "track_id": "main",
                            "name": "原片",
                            "segments": [
                                {
                                    "type": "video",
                                    "segment_id": "clip",
                                    "source_id": source_id,
                                    "timeline_start_us": 0,
                                    "timeline_duration_us": 12_000_000,
                                    "source_start_us": 0,
                                    "source_duration_us": 12_000_000,
                                    "audio_volume": 0,
                                }
                            ],
                        },
                    },
                    {
                        "type": "add_track",
                        "track": {
                            "type": "text",
                            "track_id": "captions",
                            "name": "原文",
                            "segments": [
                                {
                                    "type": "text",
                                    "segment_id": f"cue-{cue_index}",
                                    "start_us": cue_index * 6_000_000,
                                    "duration_us": 6_000_000,
                                    "text": text,
                                }
                                for cue_index, text in enumerate(cues)
                            ],
                        },
                    },
                ],
            },
        )
        translated_job = await self.submit(
            path + "/localization/translate",
            {
                "base_version": 2,
                "target_language": "en",
                "glossary": {"CamCat": "CamCat"},
            },
        )
        cancellation = None
        if index == 0 and batch_number == 1:
            cancelled = await self.request(
                "POST", "/api/v1/jobs/" + translated_job["job_id"] + "/cancel"
            )
            await self.wait_job(translated_job["job_id"], cancelled=True)
            cancellation = {
                "job_id": translated_job["job_id"],
                "initial_state": cancelled["status"],
            }
            translated_job = await self.request(
                "POST", "/api/v1/jobs/" + translated_job["job_id"] + "/retry"
            )
            self.active_jobs.add(translated_job["job_id"])
        translated = await self.wait_job(translated_job["job_id"])
        await self.request(
            "POST", path + f"/localization/{translated['job_id']}/apply", json={"base_version": 2}
        )
        dubbed_job = await self.submit(path + "/localization/dub", {"base_version": 3})
        dubbed = await self.wait_job(dubbed_job["job_id"])
        await self.request(
            "POST", path + f"/localization/{dubbed['job_id']}/apply", json={"base_version": 3}
        )
        render_job = await self.submit(
            path + "/render", {"base_version": 4, "quality_profile": "preview"}
        )
        rendered = await self.wait_job(render_job["job_id"])
        artifact = self.out / f"batch-{batch_number:02}-client-{index + 1}.mp4"
        response = await self.client.get(rendered["result"]["download_url"])
        if response.status_code != 200:
            raise RuntimeError("Actual rendered video could not be downloaded")
        artifact.write_bytes(response.content)
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(artifact),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode:
            raise RuntimeError("FFprobe rejected an actual render")
        streams = json.loads(stdout)["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        if int(video["nb_frames"]) != 360 or not any(s["codec_type"] == "audio" for s in streams):
            raise RuntimeError("Actual render has incorrect frame count or no audio stream")
        cache_job = await self.submit(path + "/localization/dub", {"base_version": 4})
        cached = await self.wait_job(cache_job["job_id"])
        if cached["result"]["cache_hits"] != 2 or cached["result"]["tts_api_calls"] != 0:
            raise RuntimeError("Unchanged subtitle audio was not fully reused")
        context = await self.request("GET", path + "/localization")
        await self.request(
            "POST",
            path + "/commands",
            json={
                "base_version": 4,
                "reason": "Verify stale speech export guard",
                "commands": [
                    {
                        "type": "update_text_segment",
                        "segment_id": context["cues"][0]["cue_id"],
                        "text": "This subtitle was edited after speech generation.",
                    }
                ],
            },
        )
        rejected = await self.client.post(path + "/render", json={"base_version": 5})
        if rejected.status_code != 422:
            raise RuntimeError("Changed subtitles were exported with stale speech")
        await self.request("DELETE", path)
        self.sessions.discard(session_id)
        translation_timing = self.timing(translated)
        translation_timing["includes_explicit_retry"] = bool(cancellation)
        return {
            "client": index + 1,
            "session_id": session_id,
            "wall_seconds": round(time.monotonic() - began, 3),
            "cancellation_retry": cancellation,
            "translation": translation_timing,
            "dubbing": self.timing(dubbed),
            "render": self.timing(rendered),
            "cached_dubbing": self.timing(cached),
            "artifact": str(artifact),
            "frames": 360,
            "stale_audio_status": rejected.status_code,
        }

    async def batch(self, number: int) -> None:
        self.save("translation_speech_render_batch", batch=number)
        began = time.monotonic()
        pending = asyncio.gather(
            *(self.pipeline(index, number) for index in range(self.args.clients)),
            return_exceptions=True,
        )
        next_resources = time.monotonic() + 30
        try:
            while not pending.done():
                await asyncio.wait([pending], timeout=5)
                if not pending.done():
                    await self.health()
                    if time.monotonic() >= next_resources:
                        await self.resources()
                        next_resources = time.monotonic() + 30
                    if time.monotonic() - self.last_saved >= 10:
                        self.save(self.report["phase"])
            results = await pending
        except BaseException:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            raise
        failures = [
            type(result).__name__ + ": " + str(result).split("?")[0][:240]
            for result in results
            if isinstance(result, BaseException)
        ]
        self.report["batches"].append(
            {
                "number": number,
                "seconds": round(time.monotonic() - began, 3),
                "pipelines": [result for result in results if isinstance(result, dict)],
                "failures": failures,
            }
        )
        self.save("batch_complete")
        if failures:
            raise RuntimeError("; ".join(failures))

    async def cleanup(self) -> None:
        for job_id in list(self.active_jobs):
            try:
                await self.request("POST", "/api/v1/jobs/" + job_id + "/cancel")
                await self.wait_job(job_id, cancelled=True)
            except Exception:
                self.report.setdefault("cleanup_pending_jobs", []).append(job_id)
        for session_id in list(self.sessions):
            try:
                await self.request("DELETE", "/api/v1/editing/sessions/" + session_id)
                self.sessions.discard(session_id)
            except Exception:
                self.report.setdefault("cleanup_pending_sessions", []).append(session_id)

    def summarize(self) -> None:
        pipelines = [
            pipeline for batch in self.report["batches"] for pipeline in batch["pipelines"]
        ]
        stages = {}
        for name in ("translation", "dubbing", "render", "cached_dubbing"):
            jobs = [
                pipeline[name]
                for pipeline in pipelines
                if not pipeline[name].get("includes_explicit_retry")
            ]
            if jobs:
                stages[name] = {
                    "jobs": len(jobs),
                    "queue_p50_seconds": percentile([job["queue_seconds"] for job in jobs], 0.50),
                    "queue_p95_seconds": percentile([job["queue_seconds"] for job in jobs], 0.95),
                    "execution_p50_seconds": percentile(
                        [job["execution_seconds"] for job in jobs], 0.50
                    ),
                    "execution_p95_seconds": percentile(
                        [job["execution_seconds"] for job in jobs], 0.95
                    ),
                    "total_p95_seconds": percentile([job["total_seconds"] for job in jobs], 0.95),
                }
        self.report["summary"] = {
            "completed_pipelines": len(pipelines),
            "failed_pipelines": sum(len(batch["failures"]) for batch in self.report["batches"]),
            "completed_video_seconds": len(pipelines) * 12,
            "stages": stages,
        }
        rss = [
            int(sample["model_process"]["VmRSS"].split()[0]) / 1024
            for sample in self.report["resources"]
            if sample.get("model_process", {}).get("VmRSS")
        ]
        if rss:
            self.report["summary"]["model_rss_mib"] = {
                "initial": round(rss[0], 2),
                "final": round(rss[-1], 2),
                "maximum": round(max(rss), 2),
                "delta_including_warmup": round(rss[-1] - rss[0], 2),
            }

    async def run(self) -> None:
        task = asyncio.current_task()
        assert task is not None
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            self.save("preflight")
            await self.health()
            project = await self.request(
                "POST", "/api/v1/projects", json={"name": "性能验收-" + self.run_id}
            )
            self.project_id = project["project_id"]
            await self.resources()
            if self.args.mode in {"quick", "pipelines"}:
                if self.args.mode == "quick":
                    session = await self.new_session()
                    await self.read_load(session["editing_session_id"])
                await self.batch(1)
            else:
                deadline = time.monotonic() + self.args.duration
                next_batch = time.monotonic()
                next_resources = time.monotonic() + 30
                while time.monotonic() < deadline:
                    await self.health()
                    if time.monotonic() >= next_batch and deadline - time.monotonic() > 120:
                        await self.batch(len(self.report["batches"]) + 1)
                        next_batch = time.monotonic() + self.args.interval
                    if time.monotonic() >= next_resources:
                        await self.resources()
                        next_resources = time.monotonic() + 30
                    self.save("soaking")
                    await asyncio.sleep(min(5, max(0, deadline - time.monotonic())))
            await self.health()
            await self.resources()
            self.report["state"] = "completed"
        except asyncio.CancelledError:
            self.report.update(state="cancelled", error="Stopped by operator")
            raise
        except Exception as error:
            self.report.update(
                state="failed", error=type(error).__name__ + ": " + str(error).split("?")[0][:500]
            )
            raise
        finally:
            try:
                await asyncio.wait_for(self.cleanup(), timeout=45)
            except TimeoutError:
                self.report["cleanup_pending_jobs"] = sorted(self.active_jobs)
                self.report["cleanup_pending_sessions"] = sorted(self.sessions)
            self.summarize()
            self.save(self.report["state"])
            await self.client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["quick", "pipelines", "soak"])
    parser.add_argument(
        "--source-job", required=True, help="Unexpired, actually analyzed user-source job UUID"
    )
    parser.add_argument("--clients", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--duration", type=int, default=1800)
    parser.add_argument("--interval", type=int, default=240)
    parser.add_argument("--api", default="http://127.0.0.1:18960")
    parser.add_argument("--web", default="http://127.0.0.1:18962")
    args = parser.parse_args()
    asyncio.run(Measurement(args).run())


if __name__ == "__main__":
    main()
