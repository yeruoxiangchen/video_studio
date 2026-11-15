from __future__ import annotations

import pytest
from camcat.agent.narrative import (
    BoundaryCaptions,
    NarrativeFacts,
    expand_block_order,
    narrative_brief,
    narrative_materials,
    narrative_sample_times,
    subtitle_blocks,
)


@pytest.mark.parametrize("duration", [0.04, 1.0, 10.0, 90.0])
def test_narrative_frames_cover_both_ends_without_reading_outside_the_source(duration):
    times = narrative_sample_times(duration)
    assert 8 <= len(times) <= 16
    assert times == sorted(set(times))
    assert 0 <= times[0] < times[-1] < duration
    assert times[0] <= duration / 4 and times[-1] >= duration * 3 / 4


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_invalid_media_duration_is_rejected_before_extracting_frames(duration):
    with pytest.raises(ValueError, match="duration"):
        narrative_sample_times(duration)


def test_source_events_keep_actor_and_opening_closing_state_bound_to_the_original():
    facts = NarrativeFacts(
        actors=["A person wearing a green coat"],
        opening_state="The door is closed",
        events=["The person opens the door", "The person enters the room"],
        closing_state="The door is open and the person is inside",
        location="A hallway and room",
    )
    materials, aliases = narrative_materials({"17.mp4": facts, "2.mp4": facts})
    assert set(aliases.values()) == {"17.mp4", "2.mp4"}
    assert all(".mp4" not in m["material_id"] for m in materials)
    ids = [e["id"] for m in materials for e in m["evidence"]]
    assert len(ids) == len(set(ids))
    for m in materials:
        evidence = m["evidence"]
        assert (
            next(e["text"] for e in evidence if e["kind"] == "opening_state") == facts.opening_state
        )
        assert (
            next(e["text"] for e in evidence if e["kind"] == "closing_state") == facts.closing_state
        )
        assert [e["text"] for e in evidence if e["kind"] == "events"] == facts.events
        assert all(e["id"].startswith(m["material_id"] + "_") for e in evidence)


def test_observed_source_identity_survives_input_enumeration_changes():
    facts = NarrativeFacts(
        actors=[],
        opening_state="An empty room",
        events=["The light turns on"],
        closing_state="The room is lit",
        location="A room",
    )
    first = narrative_materials({"uploaded-b": facts, "uploaded-a": facts})
    second = narrative_materials({"uploaded-a": facts, "uploaded-b": facts})
    assert first == second


def test_burned_in_dialogue_stays_bound_to_its_actual_source():
    facts = NarrativeFacts(
        actors=[],
        opening_state="A person outside a door",
        events=["The person enters"],
        closing_state="The person is indoors",
        location="A doorway",
        visible_dialogue=["Please come inside.", "Thank you."],
    )
    materials, _ = narrative_materials({"upload-a": facts})
    assert [e["text"] for e in materials[0]["evidence"] if e["kind"] == "visible_dialogue"] == [
        "Please come inside.",
        "Thank you.",
    ]


def test_a_unique_caption_crossing_a_cut_keeps_those_originals_adjacent():
    blocks = subtitle_blocks(
        {"arrival", "travel", "unrelated"},
        {
            "travel": BoundaryCaptions(closing_text="We are almost there."),
            "arrival": BoundaryCaptions(opening_text="We are almost there!"),
        },
    )
    assert ("travel", "arrival") in blocks
    assert ("unrelated",) in blocks
    assert sorted(s for block in blocks for s in block) == ["arrival", "travel", "unrelated"]


@pytest.mark.parametrize("text", ["", "Yes", "Thank you", "EXIT-312"])
def test_empty_or_common_short_text_cannot_force_a_narrative_join(text):
    blocks = subtitle_blocks(
        {"one", "two"},
        {"one": BoundaryCaptions(closing_text=text), "two": BoundaryCaptions(opening_text=text)},
    )
    assert all(len(block) == 1 for block in blocks)


def test_repeated_subtitles_cannot_arbitrarily_choose_between_multiple_sources():
    blocks = subtitle_blocks(
        {"one", "two", "three"},
        {
            "one": BoundaryCaptions(closing_text="We are almost there."),
            "two": BoundaryCaptions(opening_text="We are almost there."),
            "three": BoundaryCaptions(opening_text="We are almost there."),
        },
    )
    assert len(blocks) == 3


def test_cyclic_dialogue_repetition_retains_every_original_without_a_forced_loop():
    blocks = subtitle_blocks(
        {"one", "two"},
        {
            "one": BoundaryCaptions(
                opening_text="We need to leave", closing_text="We are almost there"
            ),
            "two": BoundaryCaptions(
                opening_text="We are almost there", closing_text="We need to leave"
            ),
        },
    )
    assert set(blocks) == {("one",), ("two",)}


def test_caption_evidence_is_bound_to_a_supplied_original():
    with pytest.raises(ValueError, match="source"):
        subtitle_blocks({"one"}, {"unknown": BoundaryCaptions(opening_text="We are almost there")})


def test_narrative_brief_retains_the_users_synopsis_without_manifest_examples():
    instruction = (
        "# Editing request\n\n## Story overview\n\nA cyclist arrives, then opens a door.\n\n"
        "## Output format\n\nReturn the manifest and a rendered video.\n"
    )
    assert narrative_brief(instruction) == "A cyclist arrives, then opens a door."
    assert narrative_brief("按故事顺序排列这些原片") == "按故事顺序排列这些原片"


@pytest.mark.parametrize("order", [["one", "one"], ["one"], ["one", "unknown"]])
def test_a_block_plan_cannot_repeat_drop_or_invent_an_original(order):
    with pytest.raises(ValueError, match="block"):
        expand_block_order(order, {"one": ("arriving",), "two": ("opening",)}, [])


def test_chronology_draft_uses_greedy_decoding_for_fixed_observations():
    from camcat.services.narrative import NativeNarrativePlanner

    calls = []

    class UnitClient:
        def complete(self, **kwargs):
            calls.append(kwargs)
            return {"blocks": kwargs["schema"]["properties"]["blocks"]["items"]["enum"]}

    planner = NativeNarrativePlanner.__new__(NativeNarrativePlanner)
    planner.client = UnitClient()
    facts = NarrativeFacts(
        actors=[],
        opening_state="Dark room",
        events=["Light turns on"],
        closing_state="Lit room",
        location="Room",
    )
    result = planner._blocks(
        "Arrange the footage", {"lamp": facts}, {"lamp": BoundaryCaptions()}, (("lamp",),), []
    )
    assert result == ["lamp"]
    assert calls[0]["temperature"] == 0
    assert calls[0]["presence_penalty"] == 0


def test_explicit_montage_brief_uses_thematic_progression_and_story_uses_actor_states():
    from camcat.agent.narrative_prompts import block_instructions

    assert "thematic montage" in block_instructions("This commercial presents a camera.")
    assert "thematic montage" in block_instructions("这段广告介绍照相机的功能。")
    assert "ALREADY in a changed state" in block_instructions(
        "A courier enters the building and hands a package to the guard."
    )


def test_block_expansion_preserves_subtitle_joins_and_confirmed_state_corrections():
    blocks = {"one": ("travel", "arrival"), "two": ("door",)}
    assert expand_block_order(["one", "two"], blocks, [("arrival", "door")]) == [
        "travel",
        "arrival",
        "door",
    ]
    with pytest.raises(ValueError, match="state"):
        expand_block_order(["two", "one"], blocks, [("arrival", "door")])
