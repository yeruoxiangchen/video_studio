import pytest
from camcat.agent.sequence_evidence import evidence_materials
from camcat.services.providers import (
    SourceClipSemantics,
    source_visual_observations,
    source_visual_schema,
)
from pydantic import ValidationError


@pytest.mark.parametrize("fragments", [["], ", "shot_size"], ["wide", "camera_angle"]])
def test_json_fragments_cannot_become_chronological_visual_facts(fragments):
    with pytest.raises(ValidationError, match="complete observation"):
        SourceClipSemantics(
            description="A person walks",
            scene="hall",
            composition="wide",
            risk_score=0,
            temporal_evidence=fragments,
        )


def test_source_observations_can_preserve_distinct_actors_and_multiple_shot_events():
    value = SourceClipSemantics(
        description="A masked shooter with a rifle approaches a robed person carrying a sword.",
        scene="rainy alley",
        composition="several shots",
        risk_score=0,
        people=["masked shooter, rifle", "robed person, sword", "unmasked man, coat"],
        actions=["aims", "fires", "falls", "reaches toward sword"],
        temporal_evidence=[
            "Shooter aims",
            "The robed person falls",
            "Shooter approaches",
            "Shooter reaches toward the sword",
        ],
    )
    assert len(value.people) == 3
    assert len(value.actions) == len(value.temporal_evidence) == 4


def test_actor_appearance_and_actions_survive_the_planning_evidence_boundary():
    result = evidence_materials(
        [
            {
                "material_id": "input.mp4",
                "description": "A rainy alley",
                "people": ["masked shooter carries a rifle", "robed person carries a sword"],
                "actions": ["the shooter fires toward the robed person"],
            }
        ]
    )
    text = " ".join(e["text"] for e in result[0]["evidence"])
    assert "masked shooter carries a rifle" in text
    assert "robed person carries a sword" in text
    assert "the shooter fires toward the robed person" in text


def test_observation_fields_cannot_be_omitted_and_silently_default_to_unknown():
    schema = source_visual_schema()
    assert {
        "shot_size",
        "camera_angle",
        "camera_motion",
        "subject_view",
        "subject_motion",
        "people",
        "actions",
        "opening_state",
        "closing_state",
    } <= set(schema["required"])


def test_wire_uses_explicit_boundary_states_instead_of_forcing_an_event_array_to_continue():
    schema = source_visual_schema()
    assert "temporal_evidence" not in schema["properties"]
    assert schema["properties"]["opening_state"]["maxLength"] <= 192
    assert schema["properties"]["closing_state"]["maxLength"] <= 192


def test_visible_boundary_states_become_ordered_evidence_without_changing_the_observations():
    wire = {
        "description": "A moving wheel",
        "scene": "road",
        "composition": "close view",
        "risk_score": 0,
        "opening_state": "The wheel spins on the road",
        "closing_state": "The wheel continues spinning",
    }
    canonical = SourceClipSemantics.model_validate(source_visual_observations(wire))
    assert canonical.temporal_evidence == [wire["opening_state"], wire["closing_state"]]
    assert "opening_state" in wire
