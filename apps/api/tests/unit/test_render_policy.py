from copy import deepcopy

import jsonpatch
import pytest
from camcat.domain.commands import SetRenderPolicy
from camcat.domain.project import Canvas, EditingProjectV2
from camcat.domain.reducer import reduce_commands
from camcat.domain.render_policy import RenderingPolicy
from camcat.editing.requirements import delivery_constraints
from camcat.rendering.policy import render_profile_for_project


def project():
    return EditingProjectV2(
        project_id="p",
        title="t",
        goal="g",
        canvas=Canvas(width=1280, height=720, fps_num=30, fps_den=1),
    )


def test_preserve_color_and_audio_are_native_versioned_metadata_commands():
    original = project()
    document = original.model_dump(mode="json", by_alias=True)
    reduced = reduce_commands(
        original,
        [SetRenderPolicy(policy=RenderingPolicy(preserve_color=True, preserve_audio=True))],
    )
    assert jsonpatch.apply_patch(
        deepcopy(document), reduced.operations
    ) == reduced.project.model_dump(mode="json", by_alias=True)
    assert reduced.project.metadata["render_policy"] == {
        "preserve_color": True,
        "preserve_audio": True,
    }
    assert original.metadata == {}


@pytest.mark.parametrize("quality,crf", [("preview", 28), ("standard", 20), ("high", 16)])
def test_saved_preservation_policy_controls_the_actual_render_profile(quality, crf):
    item = project().model_copy(
        update={"metadata": {"render_policy": {"preserve_color": True, "preserve_audio": True}}}
    )
    profile = render_profile_for_project(item, quality)
    assert (profile.color_contrast, profile.color_saturation, profile.color_gamma) == (1, 1, 1)
    assert profile.normalize_loudness is False
    assert profile.crf == crf


def test_default_edit_keeps_the_existing_automatic_enhancement():
    profile = render_profile_for_project(project(), "standard")
    assert profile.color_contrast == 1.035
    assert profile.color_saturation == 1.06
    assert profile.normalize_loudness is True


@pytest.mark.parametrize(
    "text,color,audio",
    [
        ("Retain the picture and original audio.", True, True),
        ("不要调色，保留原声", True, True),
        ("No color grading. Normalize audio.", True, False),
        ("Do not normalize audio.", None, True),
        ("只修改字幕", None, None),
        ("把字幕改为‘保留原声和原画’", None, None),
    ],
)
def test_preservation_constraints_are_explicit_and_separate_from_caption_text(text, color, audio):
    constraints = delivery_constraints(text)
    assert constraints.preserve_color is color
    assert constraints.preserve_audio is audio
