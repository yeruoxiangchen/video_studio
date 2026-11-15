"""Pure subtitle and speech timing contracts used by the CamCat extension."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from camcat.domain.project import EditingProjectV2, TextTrack


def hidden_duplicate_caption_ids(project: EditingProjectV2) -> set[str]:
    """Keep source lineage while rendering identical covered translations once."""
    metadata = project.metadata.get("localization") or {}
    if not isinstance(metadata, dict):
        return set()
    source_id, target_id = metadata.get("source_track_id"), metadata.get("target_track_id")
    if not isinstance(source_id, str) or not isinstance(target_id, str):
        return set()
    tracks = {track.track_id: track for track in project.timeline.tracks}
    source = tracks.get(source_id)
    target = tracks.get(target_id)
    if not isinstance(source, TextTrack) or not isinstance(target, TextTrack) or source is target:
        return set()
    return {
        original.segment_id
        for original in source.segments
        if any(
            translated.text.strip() == original.text.strip()
            and translated.start_us <= original.start_us
            and translated.start_us + translated.duration_us
            >= original.start_us + original.duration_us
            for translated in target.segments
        )
    }


class LocalizationCue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cue_id: str = Field(min_length=1, max_length=200)
    start_us: int = Field(ge=0)
    duration_us: int = Field(gt=0)
    source_text: str = Field(min_length=1, max_length=10_000)
    text: str = Field(min_length=1, max_length=10_000)


class LocalizationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    language: str = Field(min_length=2, max_length=20)
    source_track_id: str = Field(min_length=1)
    target_track_id: str = ""
    source_version: int = Field(default=1, ge=1)
    source_text_by_id: dict[str, str] = Field(default_factory=dict)
    original_audio_volumes: dict[str, float] = Field(default_factory=dict)
    translation_job_id: str | None = None
    dubbing_job_id: str | None = None
    dubbed_cues_hash: str | None = None
    subtitle_mode: Literal["bilingual", "translated"] = "bilingual"
    glossary: dict[str, str] = Field(default_factory=dict)
    translated_source_hash: str | None = None
    reviewed_version: int | None = Field(default=None, ge=1)
    reviewed_content_hash: str | None = None
    reviewed_at: str | None = None
    reviewed_by: str | None = None


def collect_cues(
    project: EditingProjectV2, *, track_id: str | None = None, check_overlap: bool = True
) -> list[LocalizationCue]:
    tracks = [track for track in project.timeline.tracks if isinstance(track, TextTrack)]
    if track_id is not None:
        tracks = [track for track in tracks if track.track_id == track_id]
    if not tracks or not tracks[0].segments:
        raise ValueError("请先在 CamCat 时间线中生成字幕，再提交译制任务")
    track = tracks[0]
    if len(track.segments) > 100:
        raise ValueError("一次译制最多处理 100 条字幕，请先缩短剪辑计划")
    metadata = project.metadata.get("localization", {})
    lineage = metadata.get("source_text_by_id", {}) if isinstance(metadata, dict) else {}
    cues = []
    previous_end = 0
    for segment in sorted(track.segments, key=lambda item: (item.start_us, item.segment_id)):
        if check_overlap and segment.start_us < previous_end:
            raise ValueError("subtitle overlap: 请先调整重叠字幕的时间，再生成配音")
        text = segment.text.strip()
        if not text:
            raise ValueError("字幕不能为空")
        cues.append(
            LocalizationCue(
                cue_id=segment.segment_id,
                start_us=segment.start_us,
                duration_us=segment.duration_us,
                source_text=str(lineage.get(segment.segment_id, text)),
                text=text,
            )
        )
        previous_end = segment.start_us + segment.duration_us
    return cues


def validate_translations(
    cues: list[LocalizationCue], response: dict[str, Any]
) -> list[LocalizationCue]:
    rows = response.get("translations")
    if not isinstance(rows, list) or len(rows) != len(cues):
        raise ValueError("翻译结果缺少字幕，请重试或缩短剪辑计划")
    expected = {cue.cue_id for cue in cues}
    translated: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid translation result")
        key, text = row.get("id"), row.get("text")
        if not isinstance(key, str) or key not in expected or key in translated:
            raise ValueError("翻译返回了未知或重复的字幕编号")
        if not isinstance(text, str) or not text.strip() or len(text) > 10_000:
            raise ValueError("翻译返回了空文本或过长文本")
        translated[key] = text.strip()
    return [cue.model_copy(update={"text": translated[cue.cue_id]}) for cue in cues]


def render_srt(cues: list[LocalizationCue]) -> str:
    def stamp(microseconds: int) -> str:
        milliseconds = (microseconds + 500) // 1000
        seconds, millis = divmod(milliseconds, 1000)
        minutes, second = divmod(seconds, 60)
        hour, minute = divmod(minutes, 60)
        return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"

    return (
        "\n\n".join(
            f"{index}\n{stamp(cue.start_us)} --> "
            f"{stamp(cue.start_us + cue.duration_us)}\n{cue.text}"
            for index, cue in enumerate(cues, 1)
        )
        + "\n"
    )


@dataclass(frozen=True)
class AudioWindow:
    speed: float
    padding_seconds: float


def fit_audio_window(
    *, natural_seconds: float, window_us: int, max_speed: float = 1.2
) -> AudioWindow:
    if not math.isfinite(natural_seconds) or natural_seconds <= 0 or window_us <= 0:
        raise ValueError("Invalid speech duration or subtitle window")
    if not math.isfinite(max_speed) or max_speed < 1:
        raise ValueError("Invalid maximum speech speed")
    seconds = window_us / 1_000_000
    speed = max(1.0, natural_seconds / seconds)
    if speed > max_speed + 1e-9:
        raise ValueError(
            f"Speech duration exceeds the subtitle window: {natural_seconds:.3f}s / "
            f"{seconds:.3f}s; 请缩短译文或允许一次时长调整"
        )
    return AudioWindow(speed=speed, padding_seconds=max(0.0, seconds - natural_seconds / speed))


def speech_cache_key(
    *, text: str, language: str, model: str, voice: str, provider: str, revision: str
) -> str:
    values = {
        "text": text,
        "language": language,
        "model": model,
        "voice": voice,
        "provider": provider,
        "revision": revision,
    }
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def cue_fingerprint(cues: list[LocalizationCue]) -> str:
    values = [
        {
            "id": cue.cue_id,
            "start_us": cue.start_us,
            "duration_us": cue.duration_us,
            "text": cue.text,
        }
        for cue in cues
    ]
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
