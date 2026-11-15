"""Run six frozen business cases once through real local services; keep every failure."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from scripts.small_eval_checks import check, check_edit, check_translation, clips, passed, tracks

SOURCE = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("CAMCAT_STUDIO_DATA", "/data/zjr/camcat_studio"))
MANIFEST = SOURCE / "eval/small_tasks_v1.json"


def safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, str):

        def clean(match: re.Match[str]) -> str:
            url = urlsplit(match.group())
            return urlunsplit((url.scheme, url.netloc, url.path, "", ""))

        return re.sub(r"https?://[^\s\"']+", clean, value)
    return value


class SmallEvaluation:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.manifest = json.loads(MANIFEST.read_text())
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
        self.out = ROOT / "checks/small_eval" / self.run_id
        self.out.mkdir(parents=True)
        self.started = time.monotonic()
        self.last_saved = self.started
        self.jobs: set[str] = set()
        self.inflight_sessions: set[str] = set()
        self.client = httpx.AsyncClient(base_url=args.api, timeout=60, trust_env=False)
        names = (
            "apps/api/camcat/agent/graph.py",
            "apps/api/camcat/agent/contract.py",
            "apps/api/camcat/repositories.py",
            "scripts/run_small_eval.py",
            "scripts/small_eval_checks.py",
            "infra/local-models.lock.json",
            "eval/small_tasks_v1.json",
        )
        self.report: dict[str, Any] = {
            "state": "running",
            "run_id": self.run_id,
            "pid": os.getpid(),
            "started_at": datetime.now(UTC).isoformat(),
            "output": str(self.out),
            "manifest": self.manifest,
            "source_sha256": {
                n: hashlib.sha256((SOURCE / n).read_bytes()).hexdigest() for n in names
            },
            "cases": [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "group": c["group"],
                    "status": "pending",
                    "checks": [],
                    "rounds": [],
                }
                for c in self.manifest["cases"]
            ],
            "scope": "One real licensed clip; authored starting projects; four Agent editing "
            "tasks, one translation/dubbing workflow, one expected-rejection guard. "
            "No public benchmark score or population success-rate claim.",
            "manual_review_pending": [
                "translation fidelity beyond lexical checks",
                "voice naturalness",
            ],
        }
        model = json.loads((ROOT / "checks/local-models-server.json").read_text())
        self.report["existing_model_gpus"] = model.get("gpus", [])
        self.project_id = ""
        self.source_job = ""
        self.localized: dict[str, Any] | None = None

    def save(self, phase: str) -> None:
        self.last_saved = time.monotonic()
        self.report.update(
            phase=phase,
            updated_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=round(time.monotonic() - self.started, 2),
        )
        groups = {}
        for group in ("agent", "workflow", "guard"):
            rows = [c for c in self.report["cases"] if c["group"] == group]
            groups[group] = {
                "passed": sum(c["status"] == "passed" for c in rows),
                "failed": sum(c["status"] in {"failed", "error"} for c in rows),
                "total": len(rows),
                "not_run": sum(c["status"] in {"pending", "blocked"} for c in rows),
            }
        self.report["summary"] = groups
        pending = self.out / "report.tmp"
        pending.write_text(json.dumps(safe(self.report), ensure_ascii=False, indent=2) + "\n")
        pending.replace(self.out / "report.json")
        status = {
            key: self.report[key]
            for key in (
                "state",
                "run_id",
                "pid",
                "output",
                "phase",
                "updated_at",
                "elapsed_seconds",
                "summary",
            )
        }
        status["cases"] = [
            {k: c.get(k) for k in ("id", "name", "status", "seconds")} for c in self.report["cases"]
        ]
        status["active_jobs"] = sorted(self.jobs)
        status["inflight_agent_sessions"] = sorted(self.inflight_sessions)
        (self.out / "status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2) + "\n"
        )
        latest = self.out.parent / "latest.tmp"
        latest.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
        latest.replace(self.out.parent / "latest.json")
        print(json.dumps(status, ensure_ascii=False), flush=True)

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(
                f"{method} {path.split('?')[0]} HTTP {response.status_code}: "
                + str(safe(response.text))[:400]
            )
        return response.json() if response.content else {}

    async def wait_job(self, job_id: str, *, timeout: float = 600) -> dict[str, Any]:
        self.jobs.add(job_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = await self.request("GET", "/api/v1/jobs/" + job_id)
            if job["status"] in {"succeeded", "failed", "cancelled", "dead_letter"}:
                self.jobs.discard(job_id)
                if job["status"] != "succeeded":
                    raise RuntimeError(
                        "Real job ended " + job["status"] + ": " + str(safe(job.get("error")))
                    )
                return job
            if time.monotonic() - self.last_saved > 20:
                self.save(self.report["phase"])
            await asyncio.sleep(1)
        raise RuntimeError("Real job observation exceeded its time budget")

    async def prepare(self) -> None:
        self.save("prepare_real_source")
        await self.request("GET", "/health/ready")
        model = await self.client.get("http://127.0.0.1:18969/health")
        model.raise_for_status()
        video = ROOT / "checks/reliability_20261111/performance-source-12s.mp4"
        provenance = ROOT / "demos/earth_full_20261111/provenance.json"
        if not video.is_file() or not provenance.is_file():
            raise RuntimeError("The existing licensed input and its provenance are required")
        self.report["input"] = {
            "file": str(video),
            "provenance": str(provenance),
            "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        }
        with video.open("rb") as stream:
            uploaded = await self.request(
                "POST",
                "/api/v1/source-media",
                files={"files": (video.name, stream, "video/mp4")},
                data={"analysis_mode": "keyframes"},
            )
        self.source_job = uploaded["job_id"]
        self.report["input"].update(
            source_job_id=self.source_job, expires_at=uploaded["expires_at"]
        )
        await self.wait_job(self.source_job)
        project = await self.request(
            "POST", "/api/v1/projects", json={"name": "小样本评测-" + self.run_id}
        )
        self.project_id = project["project_id"]
        self.report["project_id"] = self.project_id

    async def fixture(self, spec: dict[str, Any], *, localization: bool = False) -> dict[str, Any]:
        packet = await self.request(
            "POST",
            "/api/v1/editing/sessions",
            json={
                "project_id": self.project_id,
                "source_job_id": self.source_job,
                "current_goal": "小样本评测-" + spec["id"],
            },
        )
        source_id = next(
            s["source_id"] for s in packet["state"]["sources"] if s["origin"] == "user_upload"
        )
        duration = 12_000_000 if localization else 4_000_000
        width, height = spec.get("canvas", [1280, 720])
        commands: list[dict[str, Any]] = [
            {
                "type": "update_canvas",
                "canvas": {"width": width, "height": height, "fps_num": 30, "fps_den": 1},
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
                            "timeline_duration_us": duration,
                            "source_start_us": 0 if localization else 1_056_000,
                            "source_duration_us": duration,
                            "audio_volume": 0,
                        }
                    ],
                },
            },
        ]
        if spec.get("captions") or localization:
            texts = spec["source_texts"] if localization else ["欢迎使用 CamCat，让创作更简单。"]
            window = duration // len(texts)
            commands.append(
                {
                    "type": "add_track",
                    "track": {
                        "type": "text",
                        "track_id": "captions",
                        "name": "人工输入字幕",
                        "segments": [
                            {
                                "type": "text",
                                "segment_id": f"c{i + 1}",
                                "start_us": i * window,
                                "duration_us": window,
                                "text": text,
                            }
                            for i, text in enumerate(texts)
                        ],
                    },
                }
            )
        packet = await self.request(
            "POST",
            "/api/v1/editing/sessions/" + packet["editing_session_id"] + "/commands",
            json={
                "base_version": 1,
                "commands": commands,
                "reason": "Human-authored input fixture, not a model output",
            },
        )
        self.snapshot(spec["id"] + "-before", packet)
        return packet

    def snapshot(self, name: str, packet: dict[str, Any]) -> None:
        (self.out / (name + ".json")).write_text(
            json.dumps(safe(packet), ensure_ascii=False, indent=2) + "\n"
        )

    async def agent(
        self, packet: dict[str, Any], instruction: str, row: dict[str, Any]
    ) -> dict[str, Any]:
        session_id = packet["editing_session_id"]
        self.inflight_sessions.add(session_id)
        observation: dict[str, Any] = {
            "instruction": instruction,
            "base_version": packet["state_version"],
            "nodes": [],
        }
        row["rounds"].append(observation)
        started = time.monotonic()
        completed = None
        event_name = ""
        try:
            async with asyncio.timeout(750):
                async with self.client.stream(
                    "POST",
                    "/api/v1/editing/sessions/" + session_id + "/agent/stream",
                    json={
                        "base_version": packet["state_version"],
                        "instruction": instruction,
                        "top_k": 4,
                    },
                    timeout=httpx.Timeout(750, read=40),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if time.monotonic() - self.last_saved > 20:
                            self.save(row["id"])
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line[5:])
                            if data.get("graph_run_id"):
                                observation["graph_run_id"] = data["graph_run_id"]
                            if event_name == "node_completed":
                                observation["nodes"].append(
                                    {k: data.get(k) for k in ("node", "status", "duration_ms")}
                                )
                                self.save(row["id"] + ":" + str(data.get("node")))
                            if event_name == "failed":
                                self.inflight_sessions.discard(session_id)
                                raise RuntimeError(str(safe(data.get("message"))))
                            if event_name == "completed":
                                self.inflight_sessions.discard(session_id)
                                completed = data["session"]
            if completed is None:
                raise RuntimeError("Agent stream closed without a completed artifact")
            run = await self.request("GET", "/api/v1/graph-runs/" + observation["graph_run_id"])
            observation.update(
                status=run["status"],
                scope=run["state"].get("intent", {}).get("operation_scope"),
                planning_recovery=run["state"].get("planning_recovery"),
                result_version=completed["state_version"],
            )
            self.snapshot(row["id"] + f"-round-{len(row['rounds'])}", completed)
            return completed
        finally:
            observation["seconds"] = round(time.monotonic() - started, 3)

    async def probe_render(self, packet: dict[str, Any], row: dict[str, Any]) -> None:
        submitted = await self.request(
            "POST",
            "/api/v1/editing/sessions/" + packet["editing_session_id"] + "/render",
            json={"base_version": packet["state_version"], "quality_profile": "preview"},
        )
        job = await self.wait_job(submitted["job_id"])
        response = await self.client.get(job["result"]["download_url"])
        response.raise_for_status()
        artifact = self.out / (row["id"] + ".mp4")
        artifact.write_bytes(response.content)
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(artifact),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode:
            raise RuntimeError("FFprobe rejected the actual exported file")
        media = json.loads(stdout)
        video = next(s for s in media["streams"] if s["codec_type"] == "video")
        state = packet["state"]
        end = max(s["timeline_start_us"] + s["timeline_duration_us"] for s in clips(state))
        frames = round(end * state["canvas"]["fps_num"] / state["canvas"]["fps_den"] / 1_000_000)
        row["checks"].extend(
            [
                check("export_frame_count", frames, int(video["nb_read_frames"])),
                check(
                    "export_dimensions",
                    [state["canvas"]["width"], state["canvas"]["height"]],
                    [video["width"], video["height"]],
                ),
                check(
                    "export_has_audio",
                    True,
                    any(s["codec_type"] == "audio" for s in media["streams"]),
                ),
            ]
        )
        row["artifact"] = str(artifact)
        row["media"] = {
            "duration": media["format"]["duration"],
            "frames": video["nb_read_frames"],
            "width": video["width"],
            "height": video["height"],
            "codec": video["codec_name"],
        }

    async def edit_case(self, spec: dict[str, Any], row: dict[str, Any]) -> None:
        packet = await self.fixture(spec)
        row["session_id"] = packet["editing_session_id"]
        before = packet["state"]
        if spec["id"] == "multi_turn":
            packet = await self.agent(packet, spec["instructions"][0], row)
            texts = [s["text"] for t in tracks(packet["state"], "text") for s in t["segments"]]
            canvas = packet["state"]["canvas"]
            range_keys = (
                "source_id",
                "source_start_us",
                "source_duration_us",
                "timeline_start_us",
                "timeline_duration_us",
            )
            row["checks"].extend(
                [
                    check(
                        "first_turn_portrait_ratio",
                        True,
                        abs(canvas["width"] / canvas["height"] - 9 / 16) < 0.001,
                    ),
                    check("first_turn_caption", ["一起看地球。"], texts),
                    check(
                        "first_turn_video_ranges_preserved",
                        [[s[k] for k in range_keys] for s in clips(before)],
                        [[s[k] for k in range_keys] for s in clips(packet["state"])],
                    ),
                ]
            )
            before = packet["state"]
            packet = await self.agent(packet, spec["instructions"][1], row)
            row["checks"].extend(check_edit("split", before, packet["state"]))
        else:
            packet = await self.agent(packet, spec["instructions"][0], row)
            row["checks"].extend(check_edit(spec["id"], before, packet["state"]))
        await self.probe_render(packet, row)

    async def localization(self, spec: dict[str, Any], row: dict[str, Any]) -> None:
        packet = await self.fixture(spec, localization=True)
        path = "/api/v1/editing/sessions/" + packet["editing_session_id"]
        row["session_id"] = packet["editing_session_id"]
        submitted = await self.request(
            "POST",
            path + "/localization/translate",
            json={
                "base_version": packet["state_version"],
                "target_language": "en",
                "source_track_id": "captions",
                "glossary": {"CamCat": "CamCat", "地球": "Earth"},
            },
        )
        translated = await self.wait_job(submitted["job_id"])
        row["translation"] = translated["result"]["cues"]
        row["checks"].extend(check_translation(row["translation"]))
        packet = await self.request(
            "POST",
            path + f"/localization/{translated['job_id']}/apply",
            json={"base_version": packet["state_version"]},
        )
        submitted = await self.request(
            "POST",
            path + "/localization/dub",
            json={"base_version": packet["state_version"], "allow_duration_rewrite": False},
        )
        dubbed = await self.wait_job(submitted["job_id"])
        row["speech"] = [
            {
                k: a.get(k)
                for k in (
                    "cue_id",
                    "duration_us",
                    "window_seconds",
                    "speed",
                    "padding_seconds",
                    "duration_rewritten",
                )
            }
            for a in dubbed["result"]["audio"]
        ]
        row["checks"].append(
            check(
                "speech_windows",
                True,
                len(row["speech"]) == 2
                and all(
                    abs(a["duration_us"] - a["window_seconds"] * 1_000_000) < 20_000
                    and a["speed"] <= 1.2
                    and not a["duration_rewritten"]
                    for a in row["speech"]
                ),
            )
        )
        packet = await self.request(
            "POST",
            path + f"/localization/{dubbed['job_id']}/apply",
            json={"base_version": packet["state_version"]},
        )
        self.snapshot("localization-after", packet)
        self.localized = packet
        await self.probe_render(packet, row)

    async def stale_audio(self, row: dict[str, Any]) -> None:
        if self.localized is None:
            raise RuntimeError("Localization did not produce an applied speech track")
        packet = self.localized
        path = "/api/v1/editing/sessions/" + packet["editing_session_id"]
        row["session_id"] = packet["editing_session_id"]
        context = await self.request("GET", path + "/localization")
        packet = await self.request(
            "POST",
            path + "/commands",
            json={
                "base_version": packet["state_version"],
                "reason": "Authored negative test after real dubbing",
                "commands": [
                    {
                        "type": "update_text_segment",
                        "segment_id": context["cues"][0]["cue_id"],
                        "text": "The subtitle changed after speech generation.",
                    }
                ],
            },
        )
        context = await self.request("GET", path + "/localization")
        response = await self.client.post(
            path + "/render", json={"base_version": packet["state_version"]}
        )
        current = await self.request("GET", path)
        row["checks"].extend(
            [
                check("audio_marked_outdated", True, context["audio_outdated"]),
                check("export_rejected", 422, response.status_code),
                check("actionable_message", True, "重新配音" in response.text),
                check(
                    "rejection_preserves_version", packet["state_version"], current["state_version"]
                ),
            ]
        )
        self.snapshot("stale-audio-after", current)

    async def run(self) -> None:
        task = asyncio.current_task()
        assert task is not None
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            await self.prepare()
            for spec, row in zip(self.manifest["cases"], self.report["cases"], strict=True):
                row["status"] = "running"
                self.save(row["id"])
                started = time.monotonic()
                try:
                    if row["group"] == "agent":
                        await self.edit_case(spec, row)
                    elif row["id"] == "localization":
                        await self.localization(spec, row)
                    else:
                        if self.localized is None:
                            row.update(status="blocked", error="Applied speech prerequisite failed")
                        else:
                            await self.stale_audio(row)
                    if row["status"] != "blocked":
                        row["status"] = "passed" if passed(row["checks"]) else "failed"
                except Exception as exc:
                    row.update(status="error", error=str(safe(str(exc)))[:600])
                row["seconds"] = round(time.monotonic() - started, 3)
                self.save("finished:" + row["id"])
            self.report["state"] = "completed"
        except asyncio.CancelledError:
            self.report["state"] = "cancelled"
            raise
        except Exception as exc:
            self.report.update(state="failed", error=str(safe(str(exc)))[:600])
            raise
        finally:
            for job_id in list(self.jobs):
                with suppress(httpx.HTTPError):
                    await self.client.post("/api/v1/jobs/" + job_id + "/cancel")
            self.report["cleanup_pending_jobs"] = sorted(self.jobs)
            self.report["inflight_agent_sessions"] = sorted(self.inflight_sessions)
            self.save(self.report["state"])
            await self.client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:18960")
    asyncio.run(SmallEvaluation(parser.parse_args()).run())


if __name__ == "__main__":
    main()
