import pytest
from camcat.agent.graph import (
    CaptionRewriteError,
    _planning_repair_context,
    _validate_caption_revision,
)
from camcat.domain.project import Canvas, EditingProjectV2, TextSegment, TextTrack


def test_caption_repair_context_contains_the_current_words_to_rewrite():
    project = EditingProjectV2(
        project_id="revision-context",
        title="Caption revision",
        goal="Shorten the existing caption",
        canvas=Canvas(width=1920, height=1080, fps_num=30, fps_den=1),
    )
    project.timeline.tracks.append(
        TextTrack(
            track_id="captions",
            name="Captions",
            segments=[
                TextSegment(
                    segment_id="caption",
                    start_us=0,
                    duration_us=1_020_000,
                    text="Welcome to Camcat!",
                )
            ],
        )
    )
    context = _planning_repair_context(project, [])
    assert context["tracks"][0]["segments"][0]["text"] == "Welcome to Camcat!"


@pytest.mark.parametrize(
    ("replacement", "operation", "language", "accepted"),
    [
        ("Welcome to Camcat", "shorten", None, False),
        ("欢迎来到 Camcat！", "shorten", None, False),
        ("Hi Camcat!", "shorten", None, True),
        ("欢迎来到 Camcat！", "translate", "zh", True),
        ("欢迎来到 Camcat！", "translate", "en", False),
    ],
)
def test_caption_revision_checks_wording_and_explicit_target(
    replacement: str, operation: str, language: str | None, accepted: bool
):
    before = EditingProjectV2(
        project_id="caption-contract",
        title="Caption revision",
        goal="Revise the existing caption",
        canvas=Canvas(width=1920, height=1080, fps_num=30, fps_den=1),
    )
    before.timeline.tracks.append(
        TextTrack(
            track_id="captions",
            name="Captions",
            segments=[
                TextSegment(
                    segment_id="caption",
                    start_us=0,
                    duration_us=1_000_000,
                    text="Welcome to Camcat!",
                )
            ],
        )
    )
    after = before.model_copy(deep=True)
    original = after.timeline.tracks[0].segments[0]
    after.timeline.tracks[0].segments[0] = original.model_copy(update={"text": replacement})
    if accepted:
        _validate_caption_revision(before, after, language, operation)
    else:
        with pytest.raises(CaptionRewriteError):
            _validate_caption_revision(before, after, language, operation)


@pytest.mark.parametrize(("operation", "accepted"), [("keep", True), ("translate", False)])
def test_unchanged_chinese_caption_during_structural_edit_is_not_a_new_translation(
    operation: str, accepted: bool
):
    before = EditingProjectV2(
        project_id="retained-caption",
        title="Keep the existing words while splitting video",
        goal="Split the video only",
        canvas=Canvas(width=1080, height=1920, fps_num=30, fps_den=1),
    )
    before.timeline.tracks.append(
        TextTrack(
            track_id="captions",
            name="Captions",
            segments=[
                TextSegment(
                    segment_id="caption",
                    start_us=0,
                    duration_us=4_000_000,
                    text="一起看地球。",
                )
            ],
        )
    )
    after = before.model_copy(deep=True)
    if accepted:
        _validate_caption_revision(before, after, "en", operation)
    else:
        with pytest.raises(CaptionRewriteError):
            _validate_caption_revision(before, after, "en", operation)
