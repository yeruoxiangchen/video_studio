"""Unit-only scripted choices check the native full-original assembly contract."""

import json
from copy import deepcopy

import pytest
from camcat.agent.graph import CamCatGraph, CamCatState
from camcat.domain.project import Canvas, EditingProjectV2, MediaSourceRef


def sequence_state(count=3):
    media = [
        {
            "media_id": f"upload-{i}",
            "filename": f"clip-{i}.mp4",
            "duration_seconds": 1.25 + i,
            "visual_semantics": {
                "description": f"Subject at location {i}",
                "subject_view": "back",
            },
        }
        for i in range(count)
    ]
    project = EditingProjectV2(
        project_id="sequence-project",
        goal="Use each original exactly once",
        title="Sequence",
        canvas=Canvas(width=1280, height=720, fps_num=30, fps_den=1),
        sources=[
            MediaSourceRef(
                source_id=m["media_id"],
                origin="user_upload",
                storage_key="private/input.mp4",
                retention_class="transient_4h",
                metadata=m,
            )
            for m in media
        ],
        metadata={"source_media": media},
    )
    return CamCatState(
        query_text=(
            "Reorder all clips, each clip in full at original speed. "
            "No captions or transitions. 外部素材0%。"
        ),
        intent={
            "planning_mode": "source_sequence",
            "aspect_ratio": "16:9",
            "external_material_ratio_limit": 0,
            "delivery_constraints": {"use_all_sources": True},
        },
        current_document=project.model_dump(mode="json", by_alias=True),
        source_materials=[],
        ranked_materials=[],
    )


class ChoiceModel:
    def __init__(self, orders):
        self.orders = iter(orders)
        self.requests = []

    def json_completion(self, *, system, user, response_schema=None):
        self.requests.append((system, json.loads(user), response_schema))
        return {
            "clips": [
                {"material_id": source, "reason": f"Visible action supports position {index + 1}"}
                for index, source in enumerate(next(self.orders))
            ]
        }


def test_full_original_plan_only_generates_order_and_evidence_then_native_commands():
    state = sequence_state()
    before = deepcopy(state)
    model = ChoiceModel([["clip-2.mp4", "clip-0.mp4", "clip-1.mp4"]])
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(state)
    _system, request, schema = model.requests[0]
    assert "clips" in schema["properties"]
    assert schema["properties"]["clips"]["minItems"] == 3
    assert schema["properties"]["clips"]["maxItems"] == 3
    choice = schema["$defs"]["SourceSequenceClip"]
    assert set(choice["required"]) == {"material_id", "reason"}
    assert choice["properties"]["material_id"]["enum"] == ["clip-0.mp4", "clip-1.mp4", "clip-2.mp4"]
    assert "VideoSegment" not in schema.get("$defs", {})
    assert "storage_key" not in json.dumps(request)
    assert "response_schema" not in json.dumps(request)
    segments = result["simulated_document"]["timeline"]["tracks"][0]["segments"]
    assert [s["source_id"] for s in segments] == ["upload-2", "upload-0", "upload-1"]
    assert [s["source_duration_us"] for s in segments] == [3_250_000, 1_250_000, 2_250_000]
    assert [s["timeline_start_us"] for s in segments] == [0, 3_250_000, 4_500_000]
    assert all(s["source_start_us"] == 0 and s["speed"] == 1 and s["reason"] for s in segments)
    assert state == before


def test_duplicate_selection_is_returned_for_repair_without_filling_in_an_order():
    model = ChoiceModel(
        [
            ["clip-0.mp4", "clip-0.mp4", "clip-2.mp4"],
            ["clip-1.mp4", "clip-2.mp4", "clip-0.mp4"],
        ]
    )
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(sequence_state())
    assert len(model.requests) == 2
    assert "validation_error" in model.requests[1][1]
    assert "upload-" not in json.dumps(model.requests[1][1])
    segments = result["simulated_document"]["timeline"]["tracks"][0]["segments"]
    assert [s["source_id"] for s in segments] == ["upload-1", "upload-2", "upload-0"]


def test_unknown_source_never_becomes_a_saved_plan_or_a_server_chosen_fallback():
    state = sequence_state()
    before = deepcopy(state)
    model = ChoiceModel([["other", "clip-0.mp4", "clip-1.mp4"]] * 2)
    with pytest.raises(ValueError, match="after repair"):
        CamCatGraph(llm=model, retrieval=object()).generate_commands(state)
    assert state == before
    assert len(model.requests) == 2


def test_thirteen_clip_plan_keeps_a_bounded_choice_contract():
    model = ChoiceModel([[f"clip-{i}.mp4" for i in reversed(range(13))]])
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(sequence_state(13))
    _system, request, schema = model.requests[0]
    assert len(json.dumps(request)) < 10_000
    assert len(json.dumps(schema)) < 2_500
    assert len(result["simulated_document"]["timeline"]["tracks"][0]["segments"]) == 13


def test_empty_match_reasons_require_repair():
    from camcat.agent.sequence import SourceSequenceProposal
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SourceSequenceProposal.model_validate({"clips": [{"material_id": "m01", "reason": ""}]})


def test_selection_contract_uses_short_aliases_and_maps_back_to_native_source_ids():
    model = ChoiceModel([["clip-2.mp4", "clip-0.mp4", "clip-1.mp4"]])
    CamCatGraph(llm=model, retrieval=object()).generate_commands(sequence_state())
    _system, request, schema = model.requests[0]
    assert "material_id" in request["materials"][0]
    assert "source_id" not in request["materials"][0]
    assert "source_id" not in schema["$defs"]["SourceSequenceClip"]["properties"]
    assert request["materials"][0]["material_id"] == request["materials"][0]["filename"]


def test_real_client_evidence_mode_repairs_a_cross_source_citation_before_native_commands():
    class CitationModel:
        source_evidence_enabled = True

        def __init__(self):
            self.requests = []

        def json_completion(self, *, system, user, response_schema=None):
            request = json.loads(user)
            self.requests.append(request)
            sources = request["materials"]
            assert "evidence" in sources[0]
            choices = [
                {"material_id": row["material_id"], "evidence_ids": [row["evidence"][0]["id"]]}
                for row in sources
            ]
            if len(self.requests) == 1:
                choices[0]["evidence_ids"] = [sources[1]["evidence"][0]["id"]]
            return {"clips": choices}

    model = CitationModel()
    state = sequence_state()
    before = deepcopy(state)
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(state)
    assert len(model.requests) == 2
    assert "selected source" in model.requests[1]["validation_error"]
    segments = result["simulated_document"]["timeline"]["tracks"][0]["segments"]
    assert [s["source_id"] for s in segments] == ["upload-0", "upload-1", "upload-2"]
    assert "Subject at location 0" in segments[0]["reason"]
    assert "Subject at location 1" not in segments[0]["reason"]
    assert state == before


@pytest.mark.parametrize("planning_mode", ["source_sequence", "commands"])
def test_explicit_storyboard_routes_to_constraints_and_the_configured_semantic_reranker(
    monkeypatch,
    planning_mode,
):
    from types import SimpleNamespace

    from camcat.agent.sequence import SourceSequenceClip, SourceSequenceProposal

    state = sequence_state()
    state["query_text"] = (
        "Pick one clip. Use each picked clip in full at original speed. "
        "No captions, music or transitions. 外部素材0%。"
        "\n### Slot 1\n- **description**: A person inside, facing away.\n"
    )
    state["intent"]["delivery_constraints"] = {"clip_count": 1, "full_source_clips": True}
    state["intent"]["planning_mode"] = planning_mode
    model = ChoiceModel([])
    model.source_evidence_enabled = True
    reranker = object()
    observed = {}

    def select(model_arg, reranker_arg, slots, materials):
        observed.update(model=model_arg, reranker=reranker_arg, slots=slots, materials=materials)
        return SourceSequenceProposal(
            clips=(
                SourceSequenceClip(
                    material_id="clip-1.mp4", reason="Source facts: Subject at location 1"
                ),
            )
        )

    monkeypatch.setattr("camcat.agent.graph.select_storyboard", select, raising=False)
    result = CamCatGraph(llm=model, retrieval=SimpleNamespace(reranker=reranker)).generate_commands(
        state
    )
    assert observed["model"] is model and observed["reranker"] is reranker
    assert observed["slots"][0].position == 1
    assert len(observed["materials"]) == 3
    assert not model.requests
    segments = result["simulated_document"]["timeline"]["tracks"][0]["segments"]
    assert [s["source_id"] for s in segments] == ["upload-1"]


def test_full_narrative_ordering_uses_the_optional_real_service_boundary_and_native_commands():
    from camcat.agent.sequence import SourceSequenceClip, SourceSequenceProposal

    state = sequence_state(2)
    state["query_text"] += " Arrange the narrative chronologically. No extra music."
    state["intent"]["planning_mode"] = "commands"
    model = ChoiceModel([])
    model.source_evidence_enabled = True
    calls = []

    class Narrative:
        def plan(self, **kwargs):
            calls.append(kwargs)
            return SourceSequenceProposal(
                clips=tuple(
                    SourceSequenceClip(material_id=name, reason="An observed source action")
                    for name in ["clip-1.mp4", "clip-0.mp4"]
                )
            )

    result = CamCatGraph(llm=model, retrieval=object(), narrative=Narrative()).generate_commands(
        state
    )
    assert len(calls) == 1 and not model.requests
    assert calls[0]["use_all_sources"] and calls[0]["count"] == 2
    segments = result["simulated_document"]["timeline"]["tracks"][0]["segments"]
    assert [s["source_id"] for s in segments] == ["upload-1", "upload-0"]
    assert result["planning_recovery"] == "native_multimodal_narrative"


@pytest.mark.parametrize("mode", [None, "commands"])
def test_other_editing_requests_keep_the_native_command_contract(mode):
    state = sequence_state()
    state["intent"]["planning_mode"] = mode

    class PlanCaptured(Exception):
        pass

    class NativeContractModel:
        def json_completion(self, *, system, user, response_schema=None):
            assert "commands" in response_schema["properties"]
            raise PlanCaptured

    with pytest.raises(PlanCaptured):
        CamCatGraph(llm=NativeContractModel(), retrieval=object()).generate_commands(state)
