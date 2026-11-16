"""Native commands for unchanged background beds anchored to a shortened video."""

import pytest
from camcat.domain.commands import AddAudioSegment, TrimSegment, UpdateAudioSegment
from camcat.domain.project import (
    AudioSegment,
    AudioTrack,
    Canvas,
    EditingProjectV2,
    MediaSourceRef,
    VideoSegment,
    VideoTrack,
)
from camcat.domain.reducer import reduce_commands
from camcat.editing.linked_audio import (
    linked_background_audio_commands,
    linked_source_audio_updates,
)


def documents(*, role="bgm", loop=False, fade_in=0, fade_out=0):
    before = EditingProjectV2(
        project_id="linked-bed",
        title="Bed",
        goal="Trim video",
        canvas=Canvas(width=1280, height=720, fps_num=30, fps_den=1),
    )
    before.sources.extend(
        [
            MediaSourceRef(
                source_id=name,
                origin="user_upload",
                storage_key="temporary/" + name,
                retention_class="transient_4h",
            )
            for name in ("v", "a")
        ]
    )
    before.timeline.tracks.extend(
        [
            VideoTrack(
                track_id="video",
                name="Video",
                segments=[
                    VideoSegment(
                        segment_id="v",
                        source_id="v",
                        timeline_start_us=0,
                        timeline_duration_us=4_000_000,
                        source_start_us=0,
                        source_duration_us=4_000_000,
                    )
                ],
            ),
            AudioTrack(
                track_id="audio",
                name="Audio",
                role=role,
                segments=[
                    AudioSegment(
                        segment_id="a",
                        source_id="a",
                        timeline_start_us=0,
                        timeline_duration_us=4_000_000,
                        source_start_us=500_000,
                        source_duration_us=4_000_000,
                        loop=loop,
                        fade_in_us=fade_in,
                        fade_out_us=fade_out,
                    )
                ],
            ),
        ]
    )
    after = before.model_copy(deep=True)
    after.timeline.tracks[0].segments[0] = (
        after.timeline.tracks[0]
        .segments[0]
        .model_copy(update={"timeline_duration_us": 2_000_000, "source_duration_us": 2_000_000})
    )
    return before, after


@pytest.mark.parametrize("role", ["bgm", "ambient"])
def test_background_bed_is_shortened_through_auditable_native_commands(role):
    before, after = documents(role=role)
    commands = linked_background_audio_commands(before, after)
    assert len(commands) == 1
    output = reduce_commands(after, commands).project.timeline.tracks[1].segments[0]
    assert output.timeline_duration_us == output.source_duration_us == 2_000_000
    assert output.source_start_us == 500_000
    assert before.timeline.tracks[1].segments[0].timeline_duration_us == 4_000_000
    assert after.timeline.tracks[1].segments[0].timeline_duration_us == 4_000_000


@pytest.mark.parametrize("role", ["dialogue", "sfx"])
def test_dialogue_and_effects_are_not_implicitly_trimmed(role):
    before, after = documents(role=role)
    assert linked_background_audio_commands(before, after) == []


def test_looping_music_retains_its_sample_while_its_output_and_fades_fit():
    before, after = documents(loop=True, fade_in=1_500_000, fade_out=1_500_000)
    output = (
        reduce_commands(after, linked_background_audio_commands(before, after))
        .project.timeline.tracks[1]
        .segments[0]
    )
    assert output.source_duration_us == 4_000_000
    assert output.timeline_duration_us == 2_000_000
    assert output.fade_in_us + output.fade_out_us == 2_000_000


def test_explicitly_retimed_audio_and_unshortened_video_are_left_for_validation():
    before, after = documents()
    assert linked_background_audio_commands(before, before) == []
    audio = after.timeline.tracks[1].segments[0]
    after.timeline.tracks[1].segments[0] = audio.model_copy(
        update={"timeline_duration_us": 3_000_000}
    )
    assert linked_background_audio_commands(before, after) == []


@pytest.mark.parametrize(
    "partial",
    [
        {"source_start_us": 1_000_000, "source_duration_us": 2_000_000},
        {"timeline_duration_us": 2_000_000},
        {},
    ],
)
def test_matching_source_audio_follows_a_native_trim_without_losing_its_offset(partial):
    before, _ = documents(role="dialogue")
    audio = before.timeline.tracks[1].segments[0]
    before.timeline.tracks[1].segments[0] = audio.model_copy(
        update={"source_id": "v", "source_start_us": 0}
    )
    commands = [
        TrimSegment(segment_id="v", source_start_us=1_000_000, source_duration_us=2_000_000)
    ]
    if partial:
        commands.append(UpdateAudioSegment(segment_id="a", **partial))
    actual = reduce_commands(before, linked_source_audio_updates(before, commands)).project
    actual_audio = actual.timeline.tracks[1].segments[0]
    assert actual_audio.source_start_us == 1_000_000
    assert actual_audio.source_duration_us == actual_audio.timeline_duration_us == 2_000_000
    assert before.timeline.tracks[1].segments[0].timeline_duration_us == 4_000_000


def test_explicit_conflicting_source_audio_timing_is_still_rejected():
    before, _ = documents(role="dialogue")
    audio = before.timeline.tracks[1].segments[0]
    before.timeline.tracks[1].segments[0] = audio.model_copy(
        update={"source_id": "v", "source_start_us": 0}
    )
    commands = [
        TrimSegment(segment_id="v", source_start_us=0, source_duration_us=2_000_000),
        UpdateAudioSegment(
            segment_id="a", source_duration_us=2_000_000, timeline_duration_us=3_000_000
        ),
    ]
    with pytest.raises(ValueError, match="non-looping audio source is shorter"):
        reduce_commands(before, linked_source_audio_updates(before, commands))


@pytest.mark.parametrize("loop", [False, True])
def test_unrelated_or_looping_audio_is_not_relinked_to_video(loop):
    before, _ = documents(role="dialogue", loop=loop)
    if loop:
        before.timeline.tracks[1].segments[0] = (
            before.timeline.tracks[1]
            .segments[0]
            .model_copy(update={"source_id": "v", "source_start_us": 0})
        )
    commands = [TrimSegment(segment_id="v", source_start_us=0, source_duration_us=2_000_000)]
    assert linked_source_audio_updates(before, commands) == commands


def test_new_audio_sources_are_left_for_the_registration_and_structural_plan():
    before, _ = documents()
    commands = [
        TrimSegment(segment_id="v", source_start_us=0, source_duration_us=2_000_000),
        AddAudioSegment(
            track_id="audio",
            segment=AudioSegment(
                segment_id="new-bed",
                source_id="new-library-source",
                timeline_start_us=0,
                timeline_duration_us=2_000_000,
                source_start_us=0,
                source_duration_us=2_000_000,
            ),
        ),
    ]
    assert linked_source_audio_updates(before, commands) == commands
