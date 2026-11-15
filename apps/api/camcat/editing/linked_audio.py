"""Keep untouched background beds aligned with a shortened visible video."""

from __future__ import annotations

from camcat.domain.commands import UpdateAudioSegment
from camcat.domain.project import AudioTrack, EditingProjectV2, VideoTrack
from camcat.timeline.frames import us_range_to_frames


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
