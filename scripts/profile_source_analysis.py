"""Measure one real input with the same helpers and provider as the running Worker."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from camcat.config import get_settings
from camcat.media.ffmpeg import (
    detect_scene_cuts,
    extract_audio,
    extract_clip,
    extract_thumbnail,
    measure_visual_quality,
    probe,
)
from camcat.media.segmentation import build_segments
from camcat.services.providers import (
    QwenAsrClient,
    QwenVisualAnalysisClient,
    source_visual_contract_digest,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--asr", action="store_true")
    parser.add_argument("--visual", action="store_true")
    parser.add_argument("--legacy-clip", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "state": "running",
        "stages": [],
        "pipeline": "legacy_clip" if args.legacy_clip else "direct_windows",
    }
    settings = get_settings()

    def stage(name: str, function: Any) -> Any:
        report["phase"] = name
        save()
        started = time.monotonic()
        value = function()
        elapsed = round(time.monotonic() - started, 4)
        report["stages"].append({"stage": name, "seconds": elapsed})
        print(json.dumps({"stage": name, "seconds": elapsed}), flush=True)
        save()
        return value

    def save() -> None:
        (args.output / "report.json").write_text(json.dumps(report, indent=2))

    try:
        import camcat.media.ffmpeg as helper

        report["ffmpeg_helper_sha256"] = hashlib.sha256(
            Path(helper.__file__).read_bytes()
        ).hexdigest()
        meta = stage("probe", lambda: probe(args.source))
        report["media"] = {
            "duration": meta.duration,
            "width": meta.width,
            "height": meta.height,
            "has_audio": meta.has_audio,
        }
        if args.visual:
            client = QwenVisualAnalysisClient(settings)
            semantics = stage(
                "source_visual", lambda: client.analyze_video(args.source, compact=True)
            )
            report.update(
                visual_semantics=semantics.model_dump(mode="json"),
                visual_usage=client.last_analysis_usage,
                visual_contract_sha256=source_visual_contract_digest(),
            )
        cuts = stage("scene_detection", lambda: detect_scene_cuts(args.source))
        segments = build_segments(
            duration=meta.duration,
            fixed_window=settings.segment_window_seconds,
            minimum_duration=settings.segment_minimum_seconds,
            scene_cuts=cuts,
            event_times=cuts,
        )
        report.update(scene_cuts=cuts, segment_count=len(segments))
        boundary = segments[0]
        media_input, start, end = args.source, boundary.start, boundary.end
        if args.legacy_clip:
            media_input = args.output / "clip.mp4"
            stage(
                "extract_clip",
                lambda: extract_clip(
                    args.source, media_input, start=boundary.start, end=boundary.end
                ),
            )
            start, end = 0, boundary.end - boundary.start
        stage(
            "thumbnail",
            lambda: extract_thumbnail(
                media_input, args.output / "thumbnail.jpg", at=(start + end) / 2
            ),
        )
        if meta.has_audio:
            audio = args.output / "audio.mp3"
            stage("extract_audio", lambda: extract_audio(media_input, audio, start=start, end=end))
            if args.asr:
                transcription = stage("asr", lambda: QwenAsrClient(settings).transcribe(audio))
                report["asr"] = {
                    "text_characters": len(transcription.get("text", "")),
                    "cue_count": len(transcription.get("segments", [])),
                }
        report["quality"] = stage(
            "visual_quality", lambda: measure_visual_quality(media_input, start=start, end=end)
        )
        report.update(state="completed", phase="completed")
        save()
    except Exception as error:
        report.update(state="failed", error_type=type(error).__name__, error=str(error)[:500])
        save()
        raise


if __name__ == "__main__":
    main()
