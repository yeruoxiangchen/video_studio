"""Explicit delivery requirements, checked against a proposed project before persistence."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from camcat.domain.project import EditingProjectV2, TextTrack, VideoTrack
from camcat.editing.text import instruction_clauses, mask_quoted_text, negated_prefix


class DeliveryConstraints(BaseModel):
    transitions: Literal["auto", "allow", "forbid"] = "auto"
    subtitles: Literal["auto", "allow", "forbid"] = "auto"
    full_source_clips: bool = False
    use_all_sources: bool = False
    clip_count: int | None = Field(default=None, ge=1, le=100)
    preserve_color: bool | None = None
    preserve_audio: bool | None = None


def _explicit_policy(text: str, english: str, chinese: str) -> str | None:
    text = text.replace("’", "'")
    text = re.sub(r"\b(?:do\s+not|don't)\s+(?:remove|disable)\b", "keep", text, flags=re.IGNORECASE)
    text = re.sub(r"不要(?:去掉|移除|关闭)", "保留", text)
    noun = rf"(?:\b(?:{english})\b|{chinese})"
    negative = (
        r"(?:\bno\b|\bwithout\b|\bavoid\b|\bremove\b|\bdisable\b|\bdo\s+not\b|\bdon't\b"
        rf"|不要|不加|禁止|无需|去掉|移除)[^.!?;。！？；\n]{{0,70}}?{noun}"
    )
    positive = (
        r"(?:\badd\b|\buse\b|\bkeep\b|\benable\b|\brestore\b|添加|加上|使用|保留|恢复)"
        rf"[^.!?;。！？；，,\n]{{0,20}}?{noun}"
    )
    matches = [(m.start(), "forbid") for m in re.finditer(negative, text, re.IGNORECASE)]
    for match in re.finditer(positive, text, re.IGNORECASE):
        prefix = text[max(0, match.start() - 12) : match.start()]
        if re.search(r"(?:do\s+not|don't|不要|禁止|无需)\s*$", prefix, re.IGNORECASE):
            continue
        matches.append((match.start(), "allow"))
    return max(matches)[1] if matches else None


def explicit_music_policy(text: str) -> str | None:
    return _explicit_policy(mask_quoted_text(text), "music", "音乐|配乐")


def delivery_constraints(text: str, supplied: Any = None) -> DeliveryConstraints:
    raw = supplied if isinstance(supplied, dict) else {}
    # Hard prohibitions require an explicit instruction. The model's inferred
    # style preferences must not suppress source-speech captions or other edits.
    values: dict[str, Any] = {}
    # Requiring entire source files is stronger than preserving existing clip
    # bounds. Ground it in the instruction, rather than a model's default flag.
    for key in ("use_all_sources",):
        if isinstance(raw.get(key), bool):
            values[key] = raw[key]
    if (
        isinstance(raw.get("clip_count"), int)
        and not isinstance(raw["clip_count"], bool)
        and 1 <= raw["clip_count"] <= 100
    ):
        values["clip_count"] = raw["clip_count"]
    unquoted = mask_quoted_text(text)
    for key, noun, processing in (
        (
            "preserve_color",
            r"picture|visuals?|colou?rs?|frames?|原画|画面|色彩",
            r"colou?r\s+grading|调色|色彩增强",
        ),
        (
            "preserve_audio",
            r"audio|sound|原声|音频",
            r"normaliz\w*\s+audio|audio\s+normaliz\w*|音量归一化|响度归一化",
        ),
    ):
        # Unspecified/default false in model JSON must not clear a saved preference.
        # Explicit requests to enable processing are resolved below from the text.
        if raw.get(key) is True and re.search(noun, unquoted, re.IGNORECASE):
            values[key] = True
        matches = []
        preserve = rf"(?:retain|preserve|keep|保留|保持)[^.!?;。！？；，,\n]{{0,50}}?(?:{noun})"
        for match in re.finditer(preserve, unquoted, re.IGNORECASE):
            prefix = unquoted[max(0, match.start() - 12) : match.start()]
            matches.append(
                (
                    match.start(),
                    not bool(re.search(r"(?:do\s+not|don't|不要|无需)\s*$", prefix, re.IGNORECASE)),
                )
            )
        prohibit = (
            r"(?:no\b|without\b|disable|remove|不要|无需|关闭|去掉)"
            rf"[^.!?;。！？；，,\n]{{0,20}}?(?:{processing})"
        )
        matches.extend((m.start(), True) for m in re.finditer(prohibit, unquoted, re.IGNORECASE))
        enable = rf"(?:add|enable|restore|use|允许|添加|开启|恢复)\s*(?:{processing})"
        if key == "preserve_audio":
            enable += r"|\bnormaliz\w*\s+audio"
        for match in re.finditer(enable, unquoted, re.IGNORECASE):
            prefix = unquoted[max(0, match.start() - 12) : match.start()]
            matches.append(
                (
                    match.start(),
                    bool(re.search(r"(?:do\s+not|don't|不要|无需)\s*$", prefix, re.IGNORECASE)),
                )
            )
        if matches:
            values[key] = max(matches)[1]
    for key, english, chinese in (
        ("transitions", "transitions?", "转场"),
        ("subtitles", "captions?|subtitles?", "字幕"),
    ):
        policy = _explicit_policy(unquoted, english, chinese)
        if policy:
            values[key] = policy
    whole_source = re.compile(
        r"(?:each|every)\s+(?:picked\s+|selected\s+)?clip\s+in\s+full"
        r"|\b(?:full|entire|complete)\s+(?:source\s+|original\s+)?(?:clips?|files?|videos?)\b"
        r"|\b(?:clips?|files?|videos?)\b[^.!?;\n]{0,35}\b(?:entirety|full\s+duration)\b"
        r"|完整原片范围|每个(?:片段|视频|原片).{0,8}完整",
        re.IGNORECASE,
    )
    for clause, _ in instruction_clauses(text):
        for match in whole_source.finditer(clause):
            values["full_source_clips"] = not negated_prefix(clause[: match.start()])
    all_sources = re.compile(
        r"\b(?:use|include|reorder|sequence|arrange)\s+all\s+"
        r"(?:(?:the|provided|supplied|candidate)\s+|\d+\s+)*(?:video\s+)?(?:clips|videos)\b"
        r"|\b(?:each|every)\s+(?:original\s+|source\s+)?clip\s+"
        r"(?:appears|is\s+used|must\s+appear)\s+\*{0,2}exactly\s+once\b"
        r"|(?:使用|重排|排列)全部(?:片段|原片|视频)",
        re.IGNORECASE,
    )
    for match in all_sources.finditer(unquoted):
        prefix = unquoted[max(0, match.start() - 30) : match.start()]
        values["use_all_sources"] = not bool(
            re.search(r"(?:do\s+not|don't|not|不要|无需|不必|别)\s*$", prefix, re.I)
        )
    return DeliveryConstraints(**values)


def validate_delivery(
    project: EditingProjectV2,
    constraints: DeliveryConstraints,
    source_durations: dict[str, int],
    primary_source_ids: set[str],
) -> None:
    if constraints.transitions == "forbid" and project.timeline.transitions:
        raise ValueError("user forbids transitions; return a plan without transitions")
    if constraints.subtitles == "forbid" and any(
        isinstance(track, TextTrack) and track.segments for track in project.timeline.tracks
    ):
        raise ValueError("user forbids subtitles; return a plan without text tracks")
    segments = [
        segment
        for track in project.timeline.tracks
        if isinstance(track, VideoTrack)
        for segment in track.segments
    ]
    if constraints.clip_count is not None and len(segments) != constraints.clip_count:
        raise ValueError(
            f"user requires {constraints.clip_count} video clips; proposed {len(segments)}"
        )
    if constraints.use_all_sources and {s.source_id for s in segments} != primary_source_ids:
        raise ValueError("user requires all uploaded sources, each present in the video timeline")
    if constraints.full_source_clips:
        for segment in segments:
            duration = source_durations.get(segment.source_id)
            if (
                segment.source_id in primary_source_ids
                and duration is not None
                and (
                    segment.source_start_us != 0
                    or segment.source_duration_us != duration
                    or segment.speed != 1
                )
            ):
                raise ValueError(
                    f"user requires full original clips: {segment.source_id} "
                    f"must use start=0, duration={duration}, speed=1"
                )
