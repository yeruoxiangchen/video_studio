"""Render-only bilingual caption pagination using font metrics and Unicode line breaks."""

from __future__ import annotations

import math
import subprocess
from functools import lru_cache

from PIL import ImageFont
from uniseg.graphemecluster import grapheme_clusters
from uniseg.linebreak import line_break_units

from camcat.domain.localization import hidden_duplicate_caption_ids
from camcat.domain.project import EditingProjectV2, TextSegment, TextStyle, TextTrack
from camcat.timeline.frames import frame_to_us, us_range_to_frames


def caption_line_height(size: int) -> int:
    # Conservative clearance for the CJK ascender/descender plus the ASS outline.
    return math.ceil(size * 1.5)


@lru_cache(maxsize=32)
def _font(family: str, size: int) -> ImageFont.FreeTypeFont:
    filename = subprocess.check_output(
        ["fc-match", "-f", "%{file}", "--", family], text=True, timeout=10
    ).strip()
    return ImageFont.truetype(filename, size)


def _lines(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines():
        current = ""
        for unit in line_break_units(paragraph):
            # Grapheme boundaries also handle an unusually long URL or unspaced word.
            units = list(grapheme_clusters(unit)) if font.getlength(unit) > width else [unit]
            for token in units:
                if current and font.getlength(current + token.rstrip()) > width:
                    lines.append(current.rstrip())
                    current = ""
                current += token if current else token.lstrip()
        if current.strip():
            lines.append(current.rstrip())
    return lines or [text.strip()]


def _pages(
    segment: TextSegment, style: TextStyle, width: int, project: EditingProjectV2
) -> list[TextSegment]:
    lines = _lines(segment.text, _font(style.font_family or "Noto Sans CJK SC", style.size), width)
    pages = ["\n".join(lines[i : i + 2]) for i in range(0, len(lines), 2)]
    canvas = project.canvas
    start, frames = us_range_to_frames(
        segment.start_us, segment.duration_us, canvas.fps_num, canvas.fps_den
    )
    if len(pages) > frames:
        raise ValueError("字幕展示时间不足，请缩短文字或增加时长")
    shown = []
    for index, text in enumerate(pages):
        first = start + frames * index // len(pages)
        last = start + frames * (index + 1) // len(pages)
        begin = frame_to_us(first, canvas.fps_num, canvas.fps_den)
        end = frame_to_us(last, canvas.fps_num, canvas.fps_den)
        shown.append(
            segment.model_copy(
                update={
                    "segment_id": f"{segment.segment_id}:page:{index}",
                    "start_us": begin,
                    "duration_us": end - begin,
                    "text": text,
                    "style": style,
                }
            )
        )
    return shown


def caption_presentation(project: EditingProjectV2) -> dict[str, list[TextSegment]]:
    metadata = project.metadata.get("localization", {})
    tracks = {t.track_id: t for t in project.timeline.tracks if isinstance(t, TextTrack)}
    source = tracks.get(metadata.get("source_track_id"))
    target = tracks.get(metadata.get("target_track_id"))
    if source is None or target is None:
        return {}
    hidden = hidden_duplicate_caption_ids(project)
    width, height = project.canvas.width, project.canvas.height
    safe_width = round(width * 0.88)
    size_cap = max(12, round(min(width, height) * 0.045))
    gap = max(6, round(height * 0.015))
    sources = {
        (s.start_us, s.duration_us): s for s in source.segments if s.segment_id not in hidden
    }
    result: dict[str, list[TextSegment]] = {}
    for translated in target.segments:
        original = sources.get((translated.start_us, translated.duration_us))
        target_size = min(translated.style.size, size_cap)
        bottom = min(translated.style.y, 0.94) * height
        target_style = translated.style.model_copy(
            update={
                "size": target_size,
                "y": bottom / height,
                "font_family": translated.style.font_family or "Noto Sans CJK SC",
                "border_width": min(translated.style.border_width, target_size / 12),
            }
        )
        result[translated.segment_id] = _pages(translated, target_style, safe_width, project)
        target_lines = max(len(page.text.splitlines()) for page in result[translated.segment_id])
        if original is not None:
            source_size = min(original.style.size, size_cap)
            source_bottom = min(
                original.style.y * height,
                bottom - target_lines * caption_line_height(target_size) - gap,
            )
            if source_bottom - 2 * caption_line_height(source_size) < height * 0.05:
                raise ValueError("双语字幕超出可用画面，请调整字幕位置")
            source_style = original.style.model_copy(
                update={
                    "size": source_size,
                    "y": source_bottom / height,
                    "font_family": original.style.font_family or "Noto Sans CJK SC",
                    "border_width": min(original.style.border_width, source_size / 12),
                }
            )
            result[original.segment_id] = _pages(original, source_style, safe_width, project)
    return result
