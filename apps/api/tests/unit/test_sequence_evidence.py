"""Unit-only checks for source-bound citations, never model-quality evidence."""

import pytest
from camcat.agent.sequence_evidence import (
    EvidenceSequenceProposal,
    evidence_materials,
    resolve_evidence,
)


def materials():
    return evidence_materials(
        [
            {
                "material_id": "outside.mp4",
                "description": "A person outside a columned building",
                "shot": {"subject_view": "back"},
                "temporal_evidence": ["Walks away outdoors"],
            },
            {
                "material_id": "inside.mp4",
                "description": "A person in a museum",
                "shot": {"subject_view": "front"},
                "temporal_evidence": ["Turns beside a display"],
            },
        ]
    )


def test_a_selection_cannot_cite_another_sources_facts():
    sources = materials()
    proposal = EvidenceSequenceProposal.model_validate(
        {
            "clips": [
                {
                    "material_id": "outside.mp4",
                    "evidence_ids": [sources[1]["evidence"][0]["id"]],
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="selected source"):
        resolve_evidence(proposal, sources)


def test_unknown_citations_cannot_become_an_edit_reason():
    proposal = EvidenceSequenceProposal.model_validate(
        {
            "clips": [
                {
                    "material_id": "outside.mp4",
                    "evidence_ids": ["invented-museum-fact"],
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="selected source"):
        resolve_evidence(proposal, materials())


def test_saved_reasons_quote_the_selected_source_instead_of_generated_match_claims():
    sources = materials()
    citation = sources[0]["evidence"][0]
    proposal = EvidenceSequenceProposal.model_validate(
        {
            "clips": [
                {
                    "material_id": "outside.mp4",
                    "evidence_ids": [citation["id"]],
                }
            ]
        }
    )
    result = resolve_evidence(proposal, sources)
    assert result.clips[0].material_id == "outside.mp4"
    assert citation["text"] in result.clips[0].reason
    assert "museum" not in result.clips[0].reason


def test_evidence_schema_binds_choices_and_citations_to_supplied_materials():
    sources = materials()
    schema = EvidenceSequenceProposal.schema_for_materials(sources, 2)
    clip = schema["$defs"]["EvidenceSequenceClip"]
    assert set(clip["required"]) == {"material_id", "evidence_ids"}
    assert set(clip["properties"]["material_id"]["enum"]) == {"outside.mp4", "inside.mp4"}
    assert set(clip["properties"]["evidence_ids"]["items"]["enum"]) == {
        e["id"] for source in sources for e in source["evidence"]
    }
    assert schema["properties"]["clips"]["minItems"] == 2
    assert schema["properties"]["clips"]["maxItems"] == 2
