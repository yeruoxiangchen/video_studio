"""Original-footage draft for direct localization, using the existing ASR mapping."""

from uuid import NAMESPACE_URL, uuid5

from camcat.domain.commands import AddTrack, DomainEditCommand
from camcat.domain.localization import collect_cues
from camcat.domain.project import EditingProjectV2, TextSegment, TextTrack, VideoSegment, VideoTrack
from camcat.domain.reducer import reduce_commands
from camcat.editing.captions import source_captions


def source_draft_commands(project: EditingProjectV2) -> list[DomainEditCommand]:
    if any(isinstance(t, TextTrack) and t.segments for t in project.timeline.tracks):
        return []
    commands: list[DomainEditCommand] = []
    if not any(isinstance(t, VideoTrack) and t.segments for t in project.timeline.tracks):
        media = {str(m.get("media_id")): m for m in project.metadata.get("source_media", [])}
        clips: list[VideoSegment] = []
        cursor = 0
        for source in project.sources:
            item = media.get(source.source_id, {})
            duration = round(float(item.get("duration_seconds", 0)) * 1_000_000)
            if duration <= 0:
                continue
            clips.append(
                VideoSegment(
                    segment_id=str(
                        uuid5(NAMESPACE_URL, f"{project.project_id}:localize:{source.source_id}")
                    ),
                    source_id=source.source_id,
                    source_start_us=0,
                    source_duration_us=duration,
                    timeline_start_us=cursor,
                    timeline_duration_us=duration,
                )
            )
            cursor += duration
        if clips:
            commands.append(
                AddTrack(
                    track=VideoTrack(track_id="localization-originals", name="原片", segments=clips)
                )
            )
    draft = reduce_commands(project, commands).project if commands else project
    # Event-context windows overlap the primary segmentation and are retrieval evidence.
    # Preserve that evidence in project metadata while using primary windows for captions.
    caption_input = draft.model_copy(
        update={
            "metadata": {
                **draft.metadata,
                "source_segments": [
                    item
                    for item in draft.metadata.get("source_segments", [])
                    if item.get("trigger_type") != "event_context"
                ],
            }
        }
    )
    cues = speech_phrases(source_captions(caption_input))
    if not cues:
        raise ValueError("当前没有识别字幕，请上传带口播的原片，或先添加原文字幕")
    commands.append(
        AddTrack(track=TextTrack(track_id="localization-source", name="识别原文", segments=cues))
    )
    collect_cues(reduce_commands(project, commands).project, track_id="localization-source")
    return commands


def speech_phrases(captions: dict[tuple[str, str, int, int], TextSegment]) -> list[TextSegment]:
    """Rejoin ASR split at visual boundaries without crossing clips or long pauses."""
    phrases: list[TextSegment] = []
    previous_clip = ""
    for key, cue in sorted(captions.items(), key=lambda pair: (pair[1].start_us, pair[0])):
        previous = phrases[-1] if phrases else None
        end = cue.start_us + cue.duration_us
        if (
            previous is not None
            and previous_clip == key[0]
            and 0 <= cue.start_us - (previous.start_us + previous.duration_us) <= 500_000
            and end - previous.start_us <= 12_000_000
            and len(previous.text) + len(cue.text) <= 200
            and not previous.text.rstrip().endswith(("。", "！", "？", ".", "!", "?"))
        ):
            boundary = previous.text[-1:] + cue.text[:1]
            separator = "" if any("\u3400" <= c <= "\u9fff" for c in boundary) else " "
            phrases[-1] = previous.model_copy(
                update={
                    "duration_us": end - previous.start_us,
                    "text": previous.text.rstrip() + separator + cue.text.lstrip(),
                }
            )
        else:
            phrases.append(cue)
        previous_clip = key[0]
    return phrases
