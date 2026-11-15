"""Map provider ASR cue timecodes onto the actual edited clips."""

from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from camcat.domain.project import EditingProjectV2, TextSegment, VideoTrack


def source_captions(project: EditingProjectV2) -> dict[tuple[str, str, int, int], TextSegment]:
    metadata_segments = cast(list[dict[str, Any]], project.metadata.get("source_segments", []))
    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in metadata_segments:
        by_source.setdefault(str(item.get("media_id")), []).append(item)

    captions: dict[tuple[str, str, int, int], TextSegment] = {}
    for track in project.timeline.tracks:
        if not isinstance(track, VideoTrack):
            continue
        for clip in track.segments:
            for source_segment in by_source.get(clip.source_id, []):
                base_us = round(float(source_segment.get("start_time", 0)) * 1_000_000)
                for cue_index, cue in enumerate(source_segment.get("transcript_cues", [])):
                    cue_start = base_us + round(float(cue.get("start", 0)) * 1_000_000)
                    cue_end = base_us + round(float(cue.get("end", 0)) * 1_000_000)
                    start = max(clip.source_start_us, cue_start)
                    end = min(clip.source_start_us + clip.source_duration_us, cue_end)
                    duration = round((end - start) / clip.speed)
                    text = str(cue.get("text") or "").strip()
                    if not text or duration <= 0:
                        continue
                    key = (clip.segment_id, clip.source_id, cue_start, cue_end)
                    captions.setdefault(
                        key,
                        TextSegment(
                            segment_id=str(
                                uuid5(
                                    NAMESPACE_URL,
                                    f"camcat-text:{clip.segment_id}:{cue_index}:{start}:{end}",
                                )
                            ),
                            start_us=clip.timeline_start_us
                            + round((start - clip.source_start_us) / clip.speed),
                            duration_us=duration,
                            text=text,
                        ),
                    )
    return captions
