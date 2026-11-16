"""Keep source sound and background beds aligned with video trims."""

from __future__ import annotations

from camcat.domain.commands import DomainEditCommand, TrimSegment, UpdateAudioSegment
from camcat.domain.project import AudioTrack, EditingProjectV2, VideoTrack
from camcat.domain.reducer import reduce_commands
from camcat.timeline.frames import us_range_to_frames


def linked_source_audio_updates(
    before: EditingProjectV2, commands: list[DomainEditCommand]
) -> list[DomainEditCommand]:
    """Keep unambiguous same-source audio linked to trims; retain explicit conflicts."""
    structural = {
        "add_audio_segment",
        "add_track",
        "remove_track",
        "insert_segment",
        "remove_segment",
        "move_segment",
        "split_segment",
        "replace_segment_media",
    }
    if any(command.type in structural for command in commands):
        return commands
    videos = [s for t in before.timeline.tracks if isinstance(t, VideoTrack) for s in t.segments]
    by_id = {s.segment_id: s for s in videos}
    trimmed = {
        command.segment_id
        for command in commands
        if isinstance(command, TrimSegment)
        and command.segment_id in by_id
        and (
            command.source_start_us != by_id[command.segment_id].source_start_us
            or command.source_duration_us != by_id[command.segment_id].source_duration_us
        )
    }
    if not trimmed:
        return commands
    after = reduce_commands(
        before, [command for command in commands if not isinstance(command, UpdateAudioSegment)]
    ).project
    revised = {
        s.segment_id: s
        for t in after.timeline.tracks
        if isinstance(t, VideoTrack)
        for s in t.segments
    }
    remaining_audio = {
        s.segment_id for t in after.timeline.tracks if isinstance(t, AudioTrack) for s in t.segments
    }
    linked: dict[str, dict[str, int]] = {}
    for track in before.timeline.tracks:
        if not isinstance(track, AudioTrack):
            continue
        for audio in track.segments:
            if audio.loop or audio.segment_id not in remaining_audio:
                continue
            matches = [
                v
                for v in videos
                if v.segment_id in trimmed
                and v.speed == 1
                and v.source_id == audio.source_id
                and v.source_start_us == audio.source_start_us
                and v.source_duration_us == audio.source_duration_us
                and v.timeline_start_us == audio.timeline_start_us
                and v.timeline_duration_us == audio.timeline_duration_us
            ]
            if len(matches) != 1:
                continue
            video = revised[matches[0].segment_id]
            linked[audio.segment_id] = {
                "source_start_us": video.source_start_us,
                "source_duration_us": video.source_duration_us,
                "timeline_start_us": video.timeline_start_us,
                "timeline_duration_us": video.timeline_duration_us,
            }
    output = []
    explicit = set()
    for command in commands:
        if isinstance(command, UpdateAudioSegment) and command.segment_id in linked:
            explicit.add(command.segment_id)
            timing = linked[command.segment_id]
            if command.loop is not True and all(
                getattr(command, field) in (None, value) for field, value in timing.items()
            ):
                command = command.model_copy(update=timing)
        output.append(command)
    output.extend(
        UpdateAudioSegment(
            segment_id=identifier,
            source_start_us=timing["source_start_us"],
            source_duration_us=timing["source_duration_us"],
            timeline_start_us=timing["timeline_start_us"],
            timeline_duration_us=timing["timeline_duration_us"],
        )
        for identifier, timing in linked.items()
        if identifier not in explicit
    )
    return output


def _video_end(project: EditingProjectV2) -> tuple[int, int]:
    segments = [s for t in project.timeline.tracks if isinstance(t, VideoTrack) for s in t.segments]
    return (
        max((s.timeline_start_us + s.timeline_duration_us for s in segments), default=0),
        max(
            (
                sum(
                    us_range_to_frames(
                        s.timeline_start_us,
                        s.timeline_duration_us,
                        project.canvas.fps_num,
                        project.canvas.fps_den,
                    )
                )
                for s in segments
            ),
            default=0,
        ),
    )


def linked_background_audio_commands(
    before: EditingProjectV2, after: EditingProjectV2
) -> list[UpdateAudioSegment]:
    old_end, old_end_frame = _video_end(before)
    new_end, _ = _video_end(after)
    if not 0 < new_end < old_end:
        return []
    originals = {
        s.segment_id: (t.role, s)
        for t in before.timeline.tracks
        if isinstance(t, AudioTrack)
        for s in t.segments
    }
    commands = []
    for track in after.timeline.tracks:
        if not isinstance(track, AudioTrack) or track.role not in {"bgm", "ambient"}:
            continue
        for audio in track.segments:
            original = originals.get(audio.segment_id)
            if original is None or original[0] != track.role or original[1] != audio:
                continue
            end = audio.timeline_start_us + audio.timeline_duration_us
            end_frame = sum(
                us_range_to_frames(
                    audio.timeline_start_us,
                    audio.timeline_duration_us,
                    before.canvas.fps_num,
                    before.canvas.fps_den,
                )
            )
            if end_frame != old_end_frame or end <= new_end or audio.timeline_start_us >= new_end:
                continue
            duration = new_end - audio.timeline_start_us
            fade_in, fade_out = audio.fade_in_us, audio.fade_out_us
            if fade_in + fade_out > duration:
                total = fade_in + fade_out
                fade_in = fade_in * duration // total
                fade_out = duration - fade_in
            commands.append(
                UpdateAudioSegment(
                    segment_id=audio.segment_id,
                    timeline_duration_us=duration,
                    source_duration_us=audio.source_duration_us
                    if audio.loop
                    else min(audio.source_duration_us, duration),
                    fade_in_us=fade_in,
                    fade_out_us=fade_out,
                )
            )
    return commands
