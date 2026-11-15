import pytest


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("No extra captions, music or transitions.", "forbid"),
        ("Do not add any transitions.", "forbid"),
        ("不要转场，直接拼接", "forbid"),
        ("不要转场，改成加上转场", "allow"),
        ("Add transitions. Actually, no transitions.", "forbid"),
        ("字幕写“不要转场”", "auto"),
        ("Don't remove transitions.", "allow"),
        ("不要去掉转场", "allow"),
    ],
)
def test_transition_constraints_preserve_negation_and_the_latest_correction(text, expected):
    from camcat.editing.requirements import delivery_constraints

    assert delivery_constraints(text).transitions == expected


@pytest.mark.parametrize(
    "text",
    [
        "保持视频的原片范围和四秒总时长。",
        "把当前片段一分为二，原片范围保持不变。",
        "Keep the existing clip's source range and total duration unchanged.",
        "Preserve the current source in/out points while changing the captions.",
    ],
)
def test_preserving_existing_source_bounds_does_not_require_the_entire_file(text):
    from camcat.editing.requirements import delivery_constraints

    assert not delivery_constraints(text, {"full_source_clips": True}).full_source_clips


def test_explicit_whole_files_still_override_model_defaults():
    from camcat.editing.requirements import delivery_constraints

    assert delivery_constraints("Use every selected clip in full.").full_source_clips


@pytest.mark.parametrize(
    "text",
    [
        "Do not use full source clips; trim the selected range.",
        "不要完整原片范围，只用选中的区间。",
    ],
)
def test_negated_whole_file_requirement_is_not_enforced(text):
    from camcat.editing.requirements import delivery_constraints

    assert not delivery_constraints(text, {"full_source_clips": True}).full_source_clips


@pytest.mark.parametrize("field", ["subtitles", "transitions"])
def test_model_cannot_invent_an_unrequested_delivery_prohibition(field):
    from camcat.editing.requirements import delivery_constraints

    result = delivery_constraints("剪成四秒竖屏短片。", {field: "forbid"})
    assert getattr(result, field) == "auto"


@pytest.mark.parametrize(
    ("instruction", "supplied", "expected"),
    [
        ("Each clip appears **exactly once** in the reconstructed narrative.", False, True),
        ("Use all 13 candidate video clips.", False, True),
        ("Each picked clip is used exactly once.", False, False),
        ("Do not use all clips.", True, False),
    ],
)
def test_every_original_once_is_distinct_from_every_selected_original_once(
    instruction, supplied, expected
):
    from camcat.editing.requirements import delivery_constraints

    assert (
        delivery_constraints(instruction, {"use_all_sources": supplied}).use_all_sources is expected
    )
