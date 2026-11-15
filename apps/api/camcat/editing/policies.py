from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from camcat.editing.text import instruction_clauses, negated_prefix

SUPPORTED_RATIOS = ("16:9", "9:16", "3:4", "4:3", "1:1")
RATIO_RESOLUTIONS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "3:4": (1080, 1440),
    "4:3": (1440, 1080),
    "1:1": (1080, 1080),
}


def expiry_for_upload(created_at: datetime) -> datetime:
    return created_at + timedelta(hours=4)


def requested_aspect_ratio(instruction: str) -> str | None:
    """Recognize an output ratio without inferring one from the source dimensions."""
    selected = None
    for clause, _corrected in instruction_clauses(instruction.lower().replace("：", ":")):
        explicit = []
        for match in re.finditer(r"(?<!\d)(16|9|3|4|1)\s*:\s*(16|9|3|4|1)(?!\d)", clause):
            ratio = f"{match[1]}:{match[2]}"
            if ratio in SUPPORTED_RATIOS and not negated_prefix(clause[: match.start()]):
                explicit.append(ratio)
        if explicit:
            selected = explicit[-1]
            continue
        if negated_prefix(clause):
            continue
        if re.search(r"保持|保留|沿用|维持|\b(?:keep|preserve)\b", clause) and re.search(
            r"画幅|比例|aspect|ratio|canvas", clause
        ):
            selected = None
            continue
        for words, ratio in (
            (
                r"\btiktok\b|\byoutube\s+shorts\b|\binstagram\s+reels\b"
                r"|\b(?:for|as)\s+(?:a\s+)?(?:shorts|reels)\b"
                r"|\b(?:shorts|reels)\s+(?:video|format)\b"
                r"|\bvertical\s+(?:video|format|frame)\b|\bportrait\s+(?:orientation|format|video)\b"
                r"|抖音|竖屏|手机全屏",
                "9:16",
            ),
            (r"小红书|rednote", "3:4"),
            (r"方形|square", "1:1"),
            (r"横屏|landscape|widescreen", "16:9"),
        ):
            if re.search(words, clause):
                selected = ratio
    return selected


def choose_aspect_ratio(instruction: str, width: int, height: int) -> str:
    requested = requested_aspect_ratio(instruction)
    if requested:
        return requested
    if width <= 0 or height <= 0:
        return "16:9"
    source = width / height
    return min(
        SUPPORTED_RATIOS,
        key=lambda value: abs(source - _ratio_value(value)),
    )


_SECOND_RANGE = (
    r"第?\s*(?P<start>\d+(?:\.\d+)?)\s*(?:秒|s(?:ec(?:ond)?s?)?)?\s*"
    r"(?:至|到|[-–—~～]|to\b|and\b)\s*第?\s*(?P<end>\d+(?:\.\d+)?)\s*"
    r"(?:秒|s(?:ec(?:ond)?s?)?)(?![a-z])"
)
_SOURCE_RANGE = re.compile(
    r"(?:原片|原视频|源视频|原始视频|原始素材|original(?:\s+(?:source|video|footage))?"
    r"|source(?:\s+(?:video|footage))?)(?:\s*(?:的|从|from\b|between\b))*\s*" + _SECOND_RANGE,
    re.IGNORECASE,
)


def explicit_source_range(instruction: str) -> tuple[int, int] | None:
    """Extract a single numeric crop of original footage, in absolute microseconds.

    Ambiguous multi-range requests and ordinary timeline/text references stay with
    the planner; quoted caption text and negated crop instructions are not edits.
    """
    requests: list[tuple[int, int]] = []
    for clause, corrected in instruction_clauses(instruction):
        matches = list(re.finditer(_SECOND_RANGE, clause, re.IGNORECASE))
        if len(matches) > 1:
            return None
        if not matches:
            continue
        match = _SOURCE_RANGE.search(clause)
        inherited_crop = corrected and bool(requests)
        if match is None and inherited_crop:
            match = matches[0]
        if match is None:
            continue
        prefix = clause[: match.start()]
        if not inherited_crop and not re.search(
            r"裁剪|截取|剪取|保留|\b(?:trim|crop|keep)\b", prefix, re.IGNORECASE
        ):
            continue
        if negated_prefix(prefix):
            continue
        start, end = (round(Decimal(match[group]) * 1_000_000) for group in ("start", "end"))
        if end <= start:
            raise ValueError("原片裁剪终点必须晚于起点")
        if inherited_crop:
            requests = [(start, end)]
        else:
            requests.append((start, end))
    return requests[0] if len(requests) == 1 else None


def resolution_for_ratio(ratio: str) -> tuple[int, int]:
    try:
        return RATIO_RESOLUTIONS[ratio]
    except KeyError as exc:
        raise ValueError(f"unsupported aspect ratio: {ratio}") from exc


def prepare_source_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the strongest representative of repeated shots without reshuffling the story."""
    items = [dict(item) for item in candidates]
    best_for_signature: dict[str, int] = {}
    for index, item in enumerate(items):
        signature = str(item.get("shot_signature") or item.get("segment_id") or index)
        incumbent_index = best_for_signature.get(signature)
        if incumbent_index is None or float(item.get("quality_score", 0)) > float(
            items[incumbent_index].get("quality_score", 0)
        ):
            best_for_signature[signature] = index
    selected = set(best_for_signature.values())
    return [item for index, item in enumerate(items) if index in selected]


def explicit_external_ratio(instruction: str) -> float:
    normalized = instruction.lower()
    match = re.search(r"(?:外部|素材库|stock)[^%\d]{0,12}(\d{1,2})\s*%", normalized)
    if match:
        return min(0.75, max(0.0, int(match.group(1)) / 100))
    if re.search(r"全部使用外部|主要使用素材库|stock[- ]?only", normalized):
        return 0.75
    return 0.25


def _ratio_value(ratio: str) -> float:
    left, right = ratio.split(":", 1)
    return int(left) / int(right)
