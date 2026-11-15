"""Real ASR, translation, bounded dubbing and video delivery on the fixed FLEURS subset."""

from __future__ import annotations

import asyncio
import csv
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.localization_eval_metrics import (
    evaluation_protocol,
    score_speech,
    score_translations,
    validate_srt,
)
from scripts.public_benchmark_protocol import SOURCE, BenchmarkRun, sha256, write_json
from scripts.run_local_integration import local_provider_url
from scripts.run_public_editing import PublicEditing
from scripts.run_small_eval import safe


def configure_host() -> None:
    from dotenv import dotenv_values

    for key, value in dotenv_values(SOURCE / ".env").items():
        if value is not None:
            os.environ[key] = value
    data = Path(os.environ["CAMCAT_STUDIO_DATA"])
    for role in (
        "EMBEDDING",
        "RERANKER",
        "LLM",
        "ASR",
        "VISUAL_ANALYSIS",
        "NARRATIVE",
        "TRANSLATION",
    ):
        key = f"CAMCAT_{role}_BASE_URL"
        if os.environ.get(key):
            os.environ[key] = local_provider_url(os.environ[key])
    os.environ.update(
        CAMCAT_RUNTIME_DIR=str(data / "runtime"),
        CAMCAT_OBJECT_STORE_ENDPOINT="http://127.0.0.1:18963",
        CAMCAT_OBJECT_STORE_PUBLIC_ENDPOINT="http://127.0.0.1:18963",
        CAMCAT_TTS_BASE_URL="http://127.0.0.1:18961",
    )


class LocalizationEvaluation(PublicEditing):
    def __init__(self, root: Path, run: BenchmarkRun) -> None:
        super().__init__(root, run)
        from camcat.config import get_settings
        from camcat.services.object_store import ObjectStore
        from camcat.services.providers import QwenAsrClient

        self.settings = get_settings()
        self.asr = QwenAsrClient(self.settings)
        self.store = ObjectStore(self.settings)
        self.project_id = ""
        self.results: dict[str, dict[str, Any]] = {}
        self.video_results: list[dict[str, Any]] = []

    def progress(self, phase: str, *, sample_id: str = "") -> None:
        self.run.save(
            phase=phase,
            sample_id=sample_id,
            completed_samples=len(self.results),
            video_completed=len(self.video_results),
            active_jobs=sorted(self.jobs),
        )

    async def new_session(self, label: str, text: str) -> dict[str, Any]:
        packet = await self.request(
            "POST",
            "/api/v1/editing/sessions",
            json={
                "project_id": self.project_id,
                "current_goal": "Public localization " + label,
            },
        )
        return await self.request(
            "POST",
            f"/api/v1/editing/sessions/{packet['editing_session_id']}/commands",
            json={
                "base_version": 1,
                "reason": "Public source transcript, reference English withheld",
                "commands": [
                    {
                        "type": "add_track",
                        "track": {
                            "type": "text",
                            "track_id": "captions",
                            "name": "原文字幕",
                            "segments": [
                                {
                                    "type": "text",
                                    "segment_id": "source-cue",
                                    "start_us": 0,
                                    "duration_us": 60_000_000,
                                    "text": text,
                                }
                            ],
                        },
                    }
                ],
            },
        )

    async def translate(self, packet: dict[str, Any]) -> tuple[dict[str, Any], str]:
        path = f"/api/v1/editing/sessions/{packet['editing_session_id']}"
        submitted = await self.request(
            "POST",
            path + "/localization/translate",
            json={
                "base_version": packet["state_version"],
                "target_language": "en",
                "source_track_id": "captions",
                "glossary": {},
            },
        )
        job = await self.wait_job(submitted["job_id"], self.run.report["phase"])
        response = await self.client.get(job["result"]["subtitle_download_url"])
        response.raise_for_status()
        text = response.content.decode("utf-8-sig")
        validate_srt(text, job["result"]["cues"])
        subtitle = self.run.output / "subtitles" / (job["job_id"] + ".srt")
        subtitle.parent.mkdir(exist_ok=True)
        subtitle.write_text(text)
        packet = await self.request(
            "POST",
            path + f"/localization/{job['job_id']}/apply",
            json={"base_version": packet["state_version"]},
        )
        return packet, " ".join(c["text"] for c in job["result"]["cues"])

    async def dub(self, packet: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        path = f"/api/v1/editing/sessions/{packet['editing_session_id']}"
        submitted = await self.request(
            "POST",
            path + "/localization/dub",
            json={
                "base_version": packet["state_version"],
                "allow_duration_rewrite": True,
                "original_audio_volume": 0,
            },
        )
        job = await self.wait_job(submitted["job_id"], self.run.report["phase"])
        packet = await self.request(
            "POST",
            path + f"/localization/{job['job_id']}/apply",
            json={"base_version": packet["state_version"]},
        )
        return packet, job["result"]

    @staticmethod
    def failure(result: dict[str, Any], stage: str, error: Exception) -> None:
        result[stage] = {
            "status": "error",
            "error_type": type(error).__name__,
            "error": str(safe(str(error)))[:500],
        }

    async def sample(self, record: dict[str, Any]) -> None:
        from camcat.localization.service import audio_duration

        identifier = record["sentence_id"]
        folder = self.run.output / "samples" / identifier
        folder.mkdir(parents=True)
        result: dict[str, Any] = {
            "sentence_id": identifier,
            "audio": record["audio_path"],
            "source_text": record["source_text"],
        }
        audio = Path(record["audio_path"])
        if sha256(audio) != record["audio_sha256"]:
            raise ValueError("Prepared public audio changed")
        window = audio_duration(audio)
        result["window_seconds"] = window
        self.progress("asr", sample_id=identifier)
        started = time.monotonic()
        try:
            transcript = self.asr.transcribe(audio)
            write_json(folder / "asr.json", transcript)
            result["asr"] = {
                "status": "succeeded",
                "text": transcript["text"],
                "language": transcript.get("language"),
                "seconds": time.monotonic() - started,
            }
            if not transcript["text"].strip():
                raise ValueError("Speech recording produced an empty transcript")
        except Exception as error:
            self.failure(result, "asr", error)
        gold_packet = None
        for stage, text in [
            ("translation_gold", record["source_text"]),
            ("translation_asr", result.get("asr", {}).get("text", "")),
        ]:
            if not text:
                result[stage] = {"status": "blocked", "reason": "source transcript unavailable"}
                continue
            self.progress(stage, sample_id=identifier)
            started = time.monotonic()
            try:
                packet = await self.new_session(identifier + ":" + stage, text)
                packet, translated = await self.translate(packet)
                result[stage] = {
                    "status": "succeeded",
                    "text": translated,
                    "session_id": packet["editing_session_id"],
                    "seconds": time.monotonic() - started,
                }
                if stage == "translation_gold":
                    gold_packet = packet
            except Exception as error:
                self.failure(result, stage, error)
                await self.cancel_pending_jobs()
        self.progress("dubbing", sample_id=identifier)
        started = time.monotonic()
        if gold_packet is None:
            result["dubbing"] = {"status": "blocked", "reason": "gold-source translation failed"}
        else:
            try:
                path = f"/api/v1/editing/sessions/{gold_packet['editing_session_id']}"
                context = await self.request("GET", path + "/localization")
                packet = await self.request(
                    "POST",
                    path + "/commands",
                    json={
                        "base_version": gold_packet["state_version"],
                        "reason": "Evaluate translated speech in the measured source audio window",
                        "commands": [
                            {
                                "type": "update_text_segment",
                                "segment_id": c["cue_id"],
                                "start_us": 0,
                                "duration_us": round(window * 1_000_000),
                            }
                            for c in context["cues"]
                        ],
                    },
                )
                packet, speech = await self.dub(packet)
                artifact = speech["audio"][0]
                wav = folder / "dubbed.wav"
                self.store.download_file(artifact["storage_key"], wav)
                final_text = " ".join(c["text"] for c in speech["cues"])
                heard = self.asr.transcribe(wav)
                write_json(folder / "speech-job-result.json", safe(speech))
                write_json(folder / "speech-asr.json", heard)
                if artifact["speed"] > 1.2 or abs(audio_duration(wav) - window) > 0.02:
                    raise ValueError("Actual speech violates its speed or duration contract")
                result["dubbing"] = {
                    "status": "succeeded",
                    "text": final_text,
                    "roundtrip_text": heard["text"],
                    "audio_path": str(wav),
                    "speed": artifact["speed"],
                    "natural_seconds": artifact["natural_seconds"],
                    "duration_rewritten": artifact["duration_rewritten"],
                    "seconds": time.monotonic() - started,
                }
            except Exception as error:
                self.failure(result, "dubbing", error)
                await self.cancel_pending_jobs()
        self.results[identifier] = result
        write_json(folder / "result.json", result)
        self.run.report["cases"] = [
            {
                "sentence_id": key,
                "stages": {
                    name: value.get(name, {}).get("status")
                    for name in ("asr", "translation_gold", "translation_asr", "dubbing")
                },
            }
            for key, value in self.results.items()
        ]
        self.progress("sample_finished", sample_id=identifier)

    async def video(self, record: dict[str, Any]) -> None:
        from camcat.localization.service import audio_duration
        from camcat.media.ffmpeg import probe

        identifier = record["sentence_id"]
        folder = self.run.output / "videos" / identifier
        folder.mkdir(parents=True)
        result: dict[str, Any] = {"sentence_id": identifier, "status": "running"}
        self.progress("video_delivery", sample_id=identifier)
        try:
            provenance = json.loads((SOURCE / "docs/open-video-library.json").read_text())[0]
            background = (
                Path(os.environ["CAMCAT_STUDIO_DATA"])
                / "runtime/acceptance"
                / provenance["filename"]
            )
            if not background.is_file():
                raise ValueError("The existing licensed background and provenance are required")
            seconds = audio_duration(Path(record["audio_path"]))
            source = folder / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(background),
                    "-i",
                    record["audio_path"],
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-t",
                    str(seconds),
                    "-vf",
                    "scale=640:360",
                    "-r",
                    "30",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "2",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(source),
                ],
                check=True,
                timeout=180,
            )
            write_json(
                folder / "provenance.json",
                {
                    "dataset": "google/fleurs",
                    "sentence_id": identifier,
                    "audio_sha256": record["audio_sha256"],
                    "background": provenance,
                    "scope": "Public speech wrapped in licensed background; derived business test",
                },
            )
            with source.open("rb") as stream:
                uploaded = await self.request(
                    "POST",
                    "/api/v1/source-media",
                    files={"files": (source.name, stream, "video/mp4")},
                    data={"analysis_mode": "keyframes"},
                )
            analyzed = await self.wait_job(uploaded["job_id"], "video_delivery:source_analysis")
            write_json(folder / "analysis.json", safe(analyzed))
            packet = await self.request(
                "POST",
                "/api/v1/editing/sessions",
                json={
                    "project_id": self.project_id,
                    "source_job_id": uploaded["job_id"],
                    "current_goal": "Public speech localization video " + identifier,
                },
            )
            media = uploaded["media"][0]
            source_id = media["media_id"]
            duration = round(probe(source).video_duration * 1_000_000)
            # Actual ASR supplies the subtitles; English references stay outside this API flow.
            speech_text = self.results[identifier].get("asr", {}).get("text", "")
            if not speech_text:
                raise ValueError("Actual ASR output unavailable for video subtitles")
            path = f"/api/v1/editing/sessions/{packet['editing_session_id']}"
            packet = await self.request(
                "POST",
                path + "/commands",
                json={
                    "base_version": packet["state_version"],
                    "reason": "Measured source plus actual ASR subtitle",
                    "commands": [
                        {
                            "type": "update_canvas",
                            "canvas": {"width": 640, "height": 360, "fps_num": 30, "fps_den": 1},
                        },
                        {
                            "type": "add_track",
                            "track": {
                                "type": "video",
                                "track_id": "main",
                                "name": "Public speech",
                                "segments": [
                                    {
                                        "type": "video",
                                        "segment_id": "clip",
                                        "source_id": source_id,
                                        "timeline_start_us": 0,
                                        "timeline_duration_us": duration,
                                        "source_start_us": 0,
                                        "source_duration_us": duration,
                                    }
                                ],
                            },
                        },
                        {
                            "type": "add_track",
                            "track": {
                                "type": "text",
                                "track_id": "captions",
                                "name": "实际识别字幕",
                                "segments": [
                                    {
                                        "type": "text",
                                        "segment_id": "source-cue",
                                        "start_us": 0,
                                        "duration_us": duration,
                                        "text": speech_text,
                                    }
                                ],
                            },
                        },
                    ],
                },
            )
            packet, translation = await self.translate(packet)
            packet, speech = await self.dub(packet)
            submitted = await self.request(
                "POST",
                path + "/render",
                json={
                    "base_version": packet["state_version"],
                    "quality_profile": "standard",
                },
            )
            rendered = await self.wait_job(submitted["job_id"], "video_delivery:render")
            response = await self.client.get(rendered["result"]["download_url"], timeout=90)
            response.raise_for_status()
            final = folder / "localized.mp4"
            final.write_bytes(response.content)
            measured = probe(final)
            if (
                not measured.has_audio
                or abs(measured.duration - duration / 1_000_000) > 1 / 30 + 0.01
            ):
                raise ValueError(
                    "Rendered public speech video violates duration or audio requirements"
                )
            cues = speech["cues"]
            timing_preserved = bool(cues) and all(
                c["start_us"] == 0 and c["duration_us"] == duration for c in cues
            )
            if not timing_preserved:
                raise ValueError("Localized subtitles differ from the measured source timing")
            frame = folder / "frame.jpg"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    str(measured.duration / 2),
                    "-i",
                    str(final),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    str(frame),
                ],
                check=True,
                timeout=60,
            )
            result.update(
                status="succeeded",
                session_id=packet["editing_session_id"],
                translation=translation,
                final_text=" ".join(c["text"] for c in cues),
                video=str(final),
                frame=str(frame),
                duration_seconds=measured.duration,
                source_job_id=uploaded["job_id"],
                cue_timing_preserved=timing_preserved,
            )
            write_json(folder / "after.json", safe(packet))
            write_json(folder / "render.json", safe(rendered))
        except Exception as error:
            result.update(
                status="error", error_type=type(error).__name__, error=str(safe(str(error)))[:500]
            )
            await self.cancel_pending_jobs()
        self.video_results.append(result)
        write_json(folder / "result.json", result)
        self.run.report["videos"] = [
            {k: r[k] for k in ("sentence_id", "status", "video", "error") if k in r}
            for r in self.video_results
        ]
        self.progress("video_finished", sample_id=identifier)

    def score(self, references: list[dict[str, Any]]) -> dict[str, Any]:
        target = {r["sentence_id"]: r["target_text"] for r in references}
        source = {r["sentence_id"]: r["source_asr_reference"] for r in references}
        quality: dict[str, Any] = {
            "asr": score_speech(
                source,
                {k: r.get("asr", {}).get("text", "") for k, r in self.results.items()},
                characters=True,
            )
        }
        for stage in ("translation_gold", "translation_asr", "dubbing"):
            quality[stage] = score_translations(
                target, {k: r.get(stage, {}).get("text", "") for k, r in self.results.items()}
            )
            quality[stage]["succeeded"] = sum(
                r.get(stage, {}).get("status") == "succeeded" for r in self.results.values()
            )
        spoken = {
            k: r.get("dubbing", {}).get("text") or r.get("translation_gold", {}).get("text", "")
            for k, r in self.results.items()
        }
        spoken = {k: text for k, text in spoken.items() if text}
        quality["dubbing_roundtrip"] = (
            score_speech(
                spoken,
                {k: self.results[k].get("dubbing", {}).get("roundtrip_text", "") for k in spoken},
                characters=False,
            )
            if spoken
            else {"total": 0}
        )
        quality["dubbing_roundtrip"]["requested"] = len(references)
        quality["duration_rewrites"] = sum(
            bool(r.get("dubbing", {}).get("duration_rewritten")) for r in self.results.values()
        )
        quality["video_delivery"] = {
            "total": 5,
            "succeeded": sum(r["status"] == "succeeded" for r in self.video_results),
        }
        write_json(self.run.output / "quality.json", quality)
        with (self.run.output / "manual-review.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as f:
            fields = [
                "sentence_id",
                "source",
                "reference",
                "asr",
                "translation",
                "final_text",
                "audio",
                "fidelity_review",
                "numbers_names_review",
                "naturalness_review",
            ]
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for ref in references[:10]:
                result = self.results[ref["sentence_id"]]
                writer.writerow(
                    {
                        "sentence_id": ref["sentence_id"],
                        "source": result["source_text"],
                        "reference": ref["target_text"],
                        "asr": result.get("asr", {}).get("text", ""),
                        "translation": result.get("translation_asr", {}).get("text", ""),
                        "final_text": result.get("dubbing", {}).get("text", ""),
                        "audio": result.get("dubbing", {}).get("audio_path", ""),
                    }
                )
        return quality


async def main() -> None:
    configure_host()
    data = Path(os.environ["CAMCAT_STUDIO_DATA"])
    config_path = evaluation_protocol(SOURCE)
    run = BenchmarkRun(data / "benchmarks", "localization_public", config_path=config_path)
    runner = LocalizationEvaluation(data / "benchmarks", run)
    try:
        prepared = json.loads(
            (data / "benchmarks/localization_public_v1/prepared-inputs.json").read_text()
        )
        reference_path = Path(prepared["references"])
        if sha256(reference_path) != prepared["references_sha256"]:
            raise ValueError("Public references changed after preparation")
        references = [json.loads(t) for t in reference_path.read_text().splitlines() if t]
        write_json(run.output / "prepared-inputs.json", prepared)
        # No reference text is added to session goals, model inputs or translation requests.
        await runner.request("GET", "/health/ready")
        project = await runner.request(
            "POST", "/api/v1/projects", json={"name": "Public localization " + str(uuid4())}
        )
        runner.project_id = project["project_id"]
        run.report.update(
            dataset="google/fleurs",
            dataset_revision=prepared["revision"],
            sample_count=20,
            evaluation_scope=json.loads(config_path.read_text()).get(
                "evaluation_scope", "frozen public subset"
            ),
            project_id=runner.project_id,
            manual_review_pending=True,
            dubbing_input=(
                "actual gold-source translation output; five videos exercise ASR-to-dubbing"
            ),
            normalization="NFKC/lowercase/punctuation; raw hypotheses retained",
        )
        for index, sample in enumerate(prepared["samples"]):
            await runner.sample(sample)
            if index < 5:
                await runner.video(sample)
        quality = runner.score(references)
        if sha256(reference_path) != prepared["references_sha256"]:
            raise ValueError("Public references changed during evaluation")
        success = all(
            r.get(s, {}).get("status") == "succeeded"
            for r in runner.results.values()
            for s in ("asr", "translation_gold", "translation_asr", "dubbing")
        )
        success &= len(runner.results) == 20 and len(runner.video_results) == 5
        success &= all(r["status"] == "succeeded" for r in runner.video_results)
        run.save(
            state="completed",
            phase="completed",
            automated_pass=success,
            quality=quality,
            manual_review=str(run.output / "manual-review.csv"),
        )
    except Exception as error:
        run.save(state="failed", phase="failed", error=str(safe(str(error)))[:500])
        raise
    finally:
        run.report["cancel_requested_jobs"] = await runner.cancel_pending_jobs()
        run.report["remaining_jobs"] = sorted(runner.jobs)
        write_json(run.output / "report.json", run.report)
        write_json(run.latest, run.report)
        await runner.client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
