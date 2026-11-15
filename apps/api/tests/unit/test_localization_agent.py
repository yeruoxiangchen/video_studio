"""Pure tool contracts and source preparation; providers are exercised separately."""

import pytest
from camcat.domain.reducer import reduce_commands
from camcat.localization.agent import LocalizationAction, may_request_localization
from camcat.localization.source_draft import source_draft_commands
from pydantic import ValidationError
from test_localization import source_project


def test_editing_instructions_keep_the_existing_planner_route():
    assert not may_request_localization("截取前四秒，加上标题")
    assert may_request_localization("把字幕翻译成英文并配音")
    assert may_request_localization("Translate to English and dub it")
    assert may_request_localization("剪掉提到配音的片段")  # Model resolves this ambiguity.


def test_tool_arguments_are_bounded_and_do_not_include_review_or_commands():
    action = LocalizationAction(action="translate_and_dub", target_language="en")
    assert action.allow_duration_rewrite is False
    with pytest.raises(ValidationError):
        LocalizationAction(action="translate", target_language="../path")
    with pytest.raises(ValidationError):
        LocalizationAction(action="dub", confirmed=True)
    with pytest.raises(ValidationError):
        LocalizationAction(action="dub", original_audio_volume=3)


def test_source_preparation_preserves_an_existing_edit():
    assert source_draft_commands(source_project()) == []


def test_source_preparation_uses_actual_asr_cues_and_original_duration():
    project = source_project().model_copy(
        update={"timeline": source_project().timeline.model_copy(update={"tracks": []})}
    )
    project = project.model_copy(
        update={
            "metadata": {
                "source_media": [{"media_id": "original", "duration_seconds": 5}],
                "source_segments": [
                    {
                        "media_id": "original",
                        "start_time": 0,
                        "transcript_cues": [{"start": 1, "end": 3, "text": "实际识别原文"}],
                    }
                ],
            }
        }
    )
    after = reduce_commands(project, source_draft_commands(project)).project
    assert after.timeline.tracks[0].segments[0].timeline_duration_us == 5_000_000
    assert after.timeline.tracks[1].segments[0].text == "实际识别原文"
    assert after.timeline.tracks[1].segments[0].start_us == 1_000_000


def test_missing_speech_does_not_invent_subtitles():
    project = source_project().model_copy(
        update={"timeline": source_project().timeline.model_copy(update={"tracks": []})}
    )
    with pytest.raises(ValueError, match="识别字幕"):
        source_draft_commands(project)


def test_concurrent_edit_is_a_terminal_task_conflict():
    from camcat.domain.state_patch import PatchConflict
    from camcat.worker import FailureCategory, _failure_category

    assert _failure_category(PatchConflict(expected_version=2, current_version=3)) == (
        FailureCategory.DETERMINISTIC_VALIDATION
    )


def test_direct_localization_excludes_overlapping_retrieval_windows_and_groups_speech():
    project = source_project().model_copy(
        update={"timeline": source_project().timeline.model_copy(update={"tracks": []})}
    )
    project = project.model_copy(
        update={
            "metadata": {
                "source_media": [{"media_id": "original", "duration_seconds": 5}],
                "source_segments": [
                    {
                        "media_id": "original",
                        "start_time": 0,
                        "trigger_type": "fixed",
                        "transcript_cues": [{"start": 1, "end": 2, "text": "实际识别"}],
                    },
                    {
                        "media_id": "original",
                        "start_time": 2,
                        "trigger_type": "scene_cut",
                        "transcript_cues": [{"start": 0.1, "end": 1, "text": "原文"}],
                    },
                    {
                        "media_id": "original",
                        "start_time": 0.5,
                        "trigger_type": "event_context",
                        "transcript_cues": [{"start": 0.5, "end": 3, "text": "重叠检索证据"}],
                    },
                ],
            }
        }
    )
    from camcat.domain.localization import collect_cues

    after = reduce_commands(project, source_draft_commands(project)).project
    cues = collect_cues(after, track_id="localization-source")
    assert len(cues) == 1
    assert cues[0].text == "实际识别原文"
    assert (cues[0].start_us, cues[0].duration_us) == (1_000_000, 2_000_000)
    assert len(after.metadata["source_segments"]) == 3
