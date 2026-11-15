"""Pure constraint/assignment checks; semantic scores are unit inputs only."""

import pytest
from camcat.agent.storyboard import StoryboardSlot, assign_storyboard, storyboard_slots


def test_explicit_commonmark_fields_are_parsed_without_output_examples():
    text = (
        "### Slot 1\n- **description**: A person walks away in a museum.\n"
        "- **shot_size**: `Medium Wide`\n- **camera_angle**: `Eye level`\n"
        "- **camera_movement**: `Dolly forward`\n"
    )
    slots = storyboard_slots(text)
    assert len(slots) == 1 and slots[0].shot_size == "Medium Wide"


def test_a_neighbouring_wider_crop_preserves_more_requested_body_coverage_than_a_narrower_one():
    slot = StoryboardSlot(
        position=1, description="A person seen from knees up", shot_size="Medium Wide"
    )
    sources = [
        {
            "material_id": "waist",
            "description": "A person from the waist up",
            "shot": {"shot_size": "medium"},
        },
        {
            "material_id": "full",
            "description": "A person with legs visible",
            "shot": {"shot_size": "wide"},
        },
    ]
    result = assign_storyboard([slot], sources, [{}], [[0.95, 0.8]])
    assert result.clips[0].material_id == "full"


def test_known_chest_crop_cannot_beat_visible_legs_by_similar_caption_alone():
    slot = StoryboardSlot(position=1, description="Knees-up composition", shot_size="medium wide")
    sources = [
        {
            "material_id": "chest",
            "description": "Chest crop",
            "shot": {"shot_size": "medium close-up"},
        },
        {"material_id": "legs", "description": "Legs visible", "shot": {"shot_size": "wide"}},
    ]
    result = assign_storyboard([slot], sources, [{}], [[0.99, 0.01]])
    assert result.clips[0].material_id == "legs"


def test_setting_and_facing_constraints_prevent_high_semantic_score_overriding_contradictions():
    slot = StoryboardSlot(position=1, description="Rear view in a museum", shot_size="Medium Wide")
    sources = [
        {
            "material_id": "outdoor",
            "description": "A columned building",
            "environment": "outdoor",
            "shot": {"subject_view": "back", "shot_size": "medium wide"},
        },
        {
            "material_id": "front",
            "description": "A museum",
            "environment": "indoor",
            "shot": {"subject_view": "front", "shot_size": "medium wide"},
        },
        {
            "material_id": "rear",
            "description": "A person in a museum",
            "environment": "indoor",
            "shot": {"subject_view": "back", "shot_size": "medium wide"},
        },
    ]
    choice = assign_storyboard(
        [slot], sources, [{"environment": "indoor", "subject_view": "back"}], [[0.99, 0.98, 0.1]]
    )
    assert choice.clips[0].material_id == "rear"
    assert "museum" in choice.clips[0].reason


def test_known_opposite_camera_motion_is_penalized_instead_of_treated_as_missing_evidence():
    slot = StoryboardSlot(
        position=1,
        description="A person with a leftward camera movement",
        shot_size="medium wide",
        camera_angle="eye level",
        camera_movement="dolly left",
    )
    sources = [
        {
            "material_id": "opposite",
            "description": "Similar wording, opposite observed motion",
            "shot": {
                "shot_size": "medium wide",
                "camera_angle": "eye level",
                "camera_motion": "track right",
            },
        },
        {
            "material_id": "matching",
            "description": "A wider crop with the requested direction",
            "shot": {
                "shot_size": "wide",
                "camera_angle": "eye level",
                "camera_motion": "track left",
            },
        },
    ]
    result = assign_storyboard([slot], sources, [{}], [[0.95, 0.1]])
    assert result.clips[0].material_id == "matching"


def test_global_assignment_preserves_unique_sources_instead_of_greedy_reuse():
    slots = [StoryboardSlot(position=i, description="A scene") for i in (1, 2)]
    sources = [{"material_id": x, "description": "A scene"} for x in ("a", "b")]
    constraints = [{"environment": "uncertain", "subject_view": "uncertain"}] * 2
    choice = assign_storyboard(slots, sources, constraints, [[0.9, 0.8], [0.7, 0.1]])
    assert [x.material_id for x in choice.clips] == ["b", "a"]


def test_all_contradictory_candidates_produce_a_reviewable_error():
    slot = StoryboardSlot(position=1, description="Indoor shot")
    with pytest.raises(ValueError, match="consistent"):
        assign_storyboard(
            [slot],
            [{"material_id": "a", "description": "Outside", "environment": "outdoor"}],
            [{"environment": "indoor", "subject_view": "uncertain"}],
            [[1.0]],
        )


def test_model_storyboard_choice_still_rejects_duplicate_and_contradictory_sources():
    from camcat.agent.sequence import SourceSequenceClip, SourceSequenceProposal
    from camcat.agent.storyboard import validate_storyboard_choice

    slots = [StoryboardSlot(position=i, description="Rear view inside a hall") for i in (1, 2)]
    sources = [
        {"material_id": "a", "environment": "indoor", "shot": {"subject_view": "back"}},
        {"material_id": "b", "environment": "indoor", "shot": {"subject_view": "back"}},
        {"material_id": "front", "environment": "indoor", "shot": {"subject_view": "front"}},
    ]
    required = [{"environment": "indoor", "subject_view": "back"}] * 2

    def proposal(ids):
        return SourceSequenceProposal(
            clips=tuple(
                SourceSequenceClip(material_id=i, reason="Observed source evidence") for i in ids
            )
        )

    valid = proposal(["a", "b"])
    assert validate_storyboard_choice(slots, sources, required, valid) == valid
    for ids in (["a", "a"], ["a", "unknown"], ["a", "front"], ["a"]):
        with pytest.raises(ValueError):
            validate_storyboard_choice(slots, sources, required, proposal(ids))


def test_visual_matching_rejects_content_contradictions_and_assigns_sources_globally():
    from camcat.agent.storyboard import visual_storyboard_assignment

    slots = [StoryboardSlot(position=i, description="Observed scene") for i in (1, 2)]
    sources = [{"material_id": name} for name in ("a", "b", "wrong")]
    matches = [
        [
            {
                "material_id": "a",
                "fit": 90,
                "contradiction": False,
                "reason": "Matching source content",
            },
            {
                "material_id": "b",
                "fit": 80,
                "contradiction": False,
                "reason": "Matching source content",
            },
            {
                "material_id": "wrong",
                "fit": 100,
                "contradiction": True,
                "reason": "Camera label matches but the depicted subject is different",
            },
        ],
        [
            {
                "material_id": "a",
                "fit": 95,
                "contradiction": False,
                "reason": "Matching source content",
            },
            {
                "material_id": "b",
                "fit": 10,
                "contradiction": False,
                "reason": "Weak visual content match",
            },
        ],
    ]
    result = visual_storyboard_assignment(slots, sources, matches)
    assert [clip.material_id for clip in result.clips] == ["b", "a"]
    matches[0][0]["material_id"] = "outside"
    with pytest.raises(ValueError):
        visual_storyboard_assignment(slots, sources, matches)
