"""Pure localization contracts; these tests do not call or simulate model services."""

from __future__ import annotations

import pytest
from camcat.domain.localization import (
    collect_cues,
    fit_audio_window,
    render_srt,
    speech_cache_key,
    validate_translations,
)
from camcat.domain.project import EditingProjectV2


def source_project() -> EditingProjectV2:
    return EditingProjectV2.model_validate(
        {
            "project_id": "project",
            "title": "CamCat edit",
            "goal": "A short narration",
            "canvas": {"width": 640, "height": 360, "fps_num": 30, "fps_den": 1},
            "sources": [
                {
                    "source_id": "original",
                    "origin": "licensed_library",
                    "storage_key": "library/original.mp4",
                    "retention_class": "library",
                }
            ],
            "timeline": {
                "tracks": [
                    {
                        "type": "video",
                        "track_id": "main",
                        "name": "Main",
                        "segments": [
                            {
                                "type": "video",
                                "segment_id": "v1",
                                "source_id": "original",
                                "source_start_us": 0,
                                "source_duration_us": 5_000_000,
                                "timeline_start_us": 0,
                                "timeline_duration_us": 5_000_000,
                            }
                        ],
                    },
                    {
                        "type": "text",
                        "track_id": "captions",
                        "name": "Subtitles",
                        "segments": [
                            {
                                "segment_id": "s1",
                                "start_us": 0,
                                "duration_us": 2_000_000,
                                "text": "你好。",
                            },
                            {
                                "segment_id": "s2",
                                "start_us": 3_000_000,
                                "duration_us": 2_000_000,
                                "text": "欢迎来到这里。",
                            },
                        ],
                    },
                ]
            },
        }
    )


def test_cues_keep_source_ids_and_edited_timeline_coordinates():
    cues = collect_cues(source_project(), track_id="captions")
    assert [(cue.cue_id, cue.start_us, cue.duration_us) for cue in cues] == [
        ("s1", 0, 2_000_000),
        ("s2", 3_000_000, 2_000_000),
    ]
    assert cues[0].source_text == cues[0].text == "你好。"


def test_overlapping_subtitles_cannot_be_dubbed_as_sequential_speech():
    document = source_project().model_dump(mode="json", by_alias=True)
    document["timeline"]["tracks"][1]["segments"][1]["start_us"] = 1_000_000
    with pytest.raises(ValueError, match="overlap"):
        collect_cues(EditingProjectV2.model_validate(document), track_id="captions")


def test_translation_ids_are_exact_and_source_text_is_retained():
    cues = collect_cues(source_project(), track_id="captions")
    result = validate_translations(
        cues,
        {"translations": [{"id": "s2", "text": "Welcome here."}, {"id": "s1", "text": "Hello."}]},
    )
    assert [cue.cue_id for cue in result] == ["s1", "s2"]
    assert result[0].text == "Hello."
    assert result[0].source_text == "你好。"


@pytest.mark.parametrize(
    "rows",
    [
        [{"id": "s1", "text": "Hello."}],
        [{"id": "s1", "text": "Hello."}, {"id": "s1", "text": "Again."}],
        [{"id": "s1", "text": "Hello."}, {"id": "unknown", "text": "Welcome."}],
        [{"id": "s1", "text": ""}, {"id": "s2", "text": "Welcome."}],
    ],
)
def test_bad_translation_results_are_rejected(rows):
    cues = collect_cues(source_project(), track_id="captions")
    with pytest.raises(ValueError):
        validate_translations(cues, {"translations": rows})


def test_srt_uses_edit_timeline_not_source_video_offsets():
    cues = collect_cues(source_project(), track_id="captions")
    subtitle = render_srt(cues)
    assert "00:00:00,000 --> 00:00:02,000" in subtitle
    assert "00:00:03,000 --> 00:00:05,000" in subtitle
    assert "你好。" in subtitle


def test_audio_fit_has_bounded_speed_and_reports_padding():
    fit = fit_audio_window(natural_seconds=5.5, window_us=5_000_000)
    assert fit.speed == pytest.approx(1.1)
    assert fit.padding_seconds == pytest.approx(0)
    short = fit_audio_window(natural_seconds=3, window_us=5_000_000)
    assert short.speed == 1
    assert short.padding_seconds == pytest.approx(2)
    with pytest.raises(ValueError, match="exceeds"):
        fit_audio_window(natural_seconds=6.1, window_us=5_000_000)


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_invalid_audio_duration_is_rejected(seconds):
    with pytest.raises(ValueError):
        fit_audio_window(natural_seconds=seconds, window_us=5_000_000)


def test_speech_cache_is_bound_to_voice_provider_and_revision():
    values = {
        "text": "Hello.",
        "language": "en",
        "voice": "voice-a",
        "model": "piper",
        "provider": "http://tts:19889",
        "revision": "1",
    }
    assert speech_cache_key(**values) == speech_cache_key(**values)
    for key, value in (("voice", "voice-b"), ("revision", "2"), ("provider", "http://other")):
        assert speech_cache_key(**{**values, key: value}) != speech_cache_key(**values)


def test_dubbing_volume_and_metadata_use_versioned_domain_commands():
    from camcat.domain.commands import SetLocalizationMetadata, SetVideoAudioVolume
    from camcat.domain.reducer import reduce_commands

    original = source_project()
    changed = reduce_commands(
        original,
        [
            SetVideoAudioVolume(segment_id="v1", audio_volume=0),
            SetLocalizationMetadata(metadata={"language": "en", "source_track_id": "captions"}),
        ],
    )
    assert changed.project.timeline.tracks[0].segments[0].audio_volume == 0
    assert changed.project.metadata["localization"]["language"] == "en"
    assert original.timeline.tracks[0].segments[0].audio_volume == 1
    assert original.metadata == {}
    assert changed.operations


def test_generated_speech_is_a_project_source_not_a_library_asset():
    from camcat.domain.project import MediaSourceRef

    source = MediaSourceRef(
        source_id="dub",
        origin="generated_audio",
        storage_key="generated/localization/session/voice.wav",
        retention_class="generated",
    )
    assert source.origin == "generated_audio"
    assert source.retention_class == "generated"


def test_compilation_keeps_the_original_audio_volume():
    from pathlib import Path
    from uuid import UUID

    from camcat.domain.commands import SetVideoAudioVolume
    from camcat.domain.reducer import reduce_commands
    from camcat.rendering.materialization import MaterializedMedia
    from camcat.timeline.compiler import TimelineCompilerV2
    from camcat.timeline.schemas import BuildMediaRef, RenderProfile

    changed = reduce_commands(
        source_project(), [SetVideoAudioVolume(segment_id="v1", audio_volume=0)]
    )
    reference = BuildMediaRef(
        media_id="original",
        storage_key="library/original.mp4",
        sha256="a" * 64,
        size=100,
        duration_us=5_000_000,
        width=640,
        height=360,
        video_codec="h264",
        audio_codec="aac",
        retention_class="library",
    )
    compiled = TimelineCompilerV2(RenderProfile()).compile(
        session_id=UUID(int=1),
        state_version=2,
        project=changed.project,
        sources={
            "original": MaterializedMedia(
                ref=reference, local_path=Path("/not-opened-by-pure-compiler")
            )
        },
    )
    assert compiled.video_tracks[0].segments[0].audio_volume == 0


def translated_project():
    from camcat.domain.reducer import reduce_commands
    from camcat.localization.plans import translation_commands

    original = source_project()
    cues = validate_translations(
        collect_cues(original, track_id="captions"),
        {"translations": [{"id": "s1", "text": "Hello."}, {"id": "s2", "text": "Welcome here."}]},
    )
    return reduce_commands(
        original,
        translation_commands(
            original,
            cues=cues,
            source_track_id="captions",
            language="en",
            source_version=1,
            job_id="translation-job",
        ),
    ).project


def test_translation_adds_a_track_and_keeps_original_subtitles():
    translated = translated_project()
    original = next(t for t in translated.timeline.tracks if t.track_id == "captions")
    assert original.segments[0].text == "你好。"
    cues = collect_cues(translated, track_id="localization-subtitles")
    assert cues[0].text == "Hello."
    assert cues[0].source_text == "你好。"
    assert translated.metadata["localization"]["translation_job_id"] == "translation-job"


def test_translation_preserves_the_users_original_audio_mix():
    from camcat.domain.commands import SetVideoAudioVolume
    from camcat.domain.reducer import reduce_commands
    from camcat.localization.plans import translation_commands

    project = reduce_commands(
        source_project(), [SetVideoAudioVolume(segment_id="v1", audio_volume=0.35)]
    ).project
    translated = reduce_commands(
        project,
        translation_commands(
            project,
            cues=collect_cues(project, track_id="captions"),
            source_track_id="captions",
            language="en",
            source_version=2,
            job_id="translation-job",
        ),
    ).project
    assert translated.timeline.tracks[0].segments[0].audio_volume == 0.35


def test_dubbing_adds_a_real_source_reference_and_detects_subtitle_edits():
    from camcat.domain.commands import UpdateTextSegment
    from camcat.domain.reducer import reduce_commands
    from camcat.localization.plans import SpeechArtifact, dubbing_commands, validate_dubbed_state

    translated = translated_project()
    cues = collect_cues(translated, track_id="localization-subtitles")
    audio = [
        SpeechArtifact(
            cue_id=c.cue_id,
            source_id=f"dub-{i}",
            storage_key=f"generated/localization/session/{i}.wav",
            duration_us=c.duration_us,
            retention_class="generated",
        )
        for i, c in enumerate(cues)
    ]
    dubbed = reduce_commands(
        translated,
        dubbing_commands(
            translated,
            cues=cues,
            audio=audio,
            language="en",
            source_version=2,
            job_id="dub-job",
            original_audio_volume=0,
        ),
    ).project
    assert dubbed.timeline.tracks[0].segments[0].audio_volume == 0
    assert any(s.origin == "generated_audio" for s in dubbed.sources)
    assert any(t.track_id == "localization-dialogue" for t in dubbed.timeline.tracks)
    validate_dubbed_state(dubbed)
    edited = reduce_commands(
        dubbed, [UpdateTextSegment(segment_id=cues[0].cue_id, text="A changed line.")]
    ).project
    with pytest.raises(ValueError, match="重新配音"):
        validate_dubbed_state(edited)


def test_retranslation_removes_old_dubbing_and_restores_source_audio():
    from camcat.domain.reducer import reduce_commands
    from camcat.localization.plans import SpeechArtifact, dubbing_commands, translation_commands

    translated = translated_project()
    cues = collect_cues(translated, track_id="localization-subtitles")
    audio = [
        SpeechArtifact(
            cue_id=c.cue_id,
            source_id=f"dub-{i}",
            storage_key=f"generated/localization/session/{i}.wav",
            duration_us=c.duration_us,
            retention_class="generated",
        )
        for i, c in enumerate(cues)
    ]
    dubbed = reduce_commands(
        translated,
        dubbing_commands(
            translated,
            cues=cues,
            audio=audio,
            language="en",
            source_version=2,
            job_id="dub-job",
            original_audio_volume=0,
        ),
    ).project
    fresh = collect_cues(dubbed, track_id="captions")
    updated = reduce_commands(
        dubbed,
        translation_commands(
            dubbed,
            cues=fresh,
            source_track_id="captions",
            language="en",
            source_version=3,
            job_id="new-translation",
        ),
    ).project
    assert not any(t.track_id == "localization-dialogue" for t in updated.timeline.tracks)
    assert updated.timeline.tracks[0].segments[0].audio_volume == 1
