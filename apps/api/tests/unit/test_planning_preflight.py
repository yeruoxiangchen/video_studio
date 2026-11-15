from __future__ import annotations

import json
from copy import deepcopy

import pytest
from camcat.agent.graph import CamCatGraph, CamCatState
from camcat.domain.project import Canvas, EditingProjectV2, MediaSourceRef


def planning_state(instruction: str) -> CamCatState:
    media = [
        {"media_id": name, "filename": f"{name}.mp4", "duration_seconds": 5.0}
        for name in ("one", "two")
    ]
    project = EditingProjectV2(
        project_id="preflight",
        goal="edit",
        title="edit",
        canvas=Canvas(width=1280, height=720, fps_num=30, fps_den=1),
        sources=[
            MediaSourceRef(
                source_id=m["media_id"],
                origin="user_upload",
                storage_key="temporary/private/" + m["media_id"],
                retention_class="transient_4h",
            )
            for m in media
        ],
        metadata={"source_media": media},
    )
    return CamCatState(
        query_text=instruction,
        current_document=project.model_dump(mode="json", by_alias=True),
        intent={"aspect_ratio": "16:9", "external_material_ratio_limit": 0.0},
        source_materials=[
            {
                "media_id": m["media_id"],
                "start_time": 0,
                "end_time": 5,
                "description_text": "visible action",
            }
            for m in media
        ],
        ranked_materials=[],
    )


class TransitionLlm:
    """Unit-only proposals verify rejection and repair of an unrenderable plan."""

    def __init__(self, *, forbid: bool):
        self.forbid = forbid
        self.requests = []

    def json_completion(self, *, system, user, response_schema=None):
        self.requests.append(json.loads(user))
        repaired = len(self.requests) > 1
        segments = [
            {
                "type": "video",
                "segment_id": f"s{index}",
                "source_id": name,
                "timeline_start_us": index * 3_000_000,
                "source_start_us": 100_000 if repaired and not self.forbid else 0,
                "source_duration_us": 3_000_000,
            }
            for index, name in enumerate(("one", "two"))
        ]
        commands = [
            {
                "type": "add_track",
                "track": {
                    "type": "video",
                    "track_id": "main",
                    "name": "main",
                    "segments": segments,
                },
            }
        ]
        if not (repaired and self.forbid):
            commands.append(
                {
                    "type": "set_transition",
                    "transition_id": "dissolve",
                    "left_segment_id": "s0",
                    "right_segment_id": "s1",
                    "transition_type": "dissolve",
                    "duration_us": 100_000,
                }
            )
        return {"commands": commands}


def test_forbidden_transition_is_repaired_before_a_plan_can_be_saved() -> None:
    model = TransitionLlm(forbid=True)
    graph = CamCatGraph(llm=model, retrieval=object())
    state = planning_state("No extra captions, music or transitions.")
    original = deepcopy(state)
    result = graph.generate_commands(state)
    assert len(model.requests) == 2
    assert "forbids transitions" in model.requests[1]["validation_error"]
    assert result["simulated_document"]["timeline"]["transitions"] == []
    assert state == original


def test_transition_handles_use_the_compiler_rules_during_planning() -> None:
    model = TransitionLlm(forbid=False)
    graph = CamCatGraph(llm=model, retrieval=object())
    result = graph.generate_commands(planning_state("Add a short dissolve transition."))
    assert len(model.requests) == 2
    assert "transition source handles are insufficient" in model.requests[1]["validation_error"]
    assert len(result["simulated_document"]["timeline"]["transitions"]) == 1


def test_zero_external_material_ratio_preserves_sources_without_a_library_call() -> None:
    class NoLibrary:
        def search(self, **kwargs):
            raise AssertionError("an originals-only edit must not call library providers")

    state = planning_state("外部素材0%，只使用用户原片")
    state["mode"] = "edit"
    state["current_document"]["metadata"]["source_segments"] = [
        {"segment_id": "one:1", "media_id": "one", "start_time": 0, "end_time": 5}
    ]
    result = CamCatGraph(llm=object(), retrieval=NoLibrary()).retrieve(state)
    assert result["ranked_materials"] == []
    assert result["source_materials"][0]["media_id"] == "one"


@pytest.mark.parametrize(
    "instruction", ["不要转场，直接拼接。", "No extra captions, music or transitions."]
)
def test_subtitle_generation_respects_an_explicit_ban(instruction: str) -> None:
    instruction += " 不要字幕。"
    state = planning_state(instruction)
    state["current_document"]["metadata"]["source_segments"] = [
        {
            "media_id": "one",
            "start_time": 0,
            "end_time": 5,
            "transcript_cues": [{"start": 0, "end": 1, "text": "spoken words"}],
        }
    ]
    result = CamCatGraph(llm=TransitionLlm(forbid=True), retrieval=object()).generate_commands(
        state
    )
    output = CamCatGraph(llm=object(), retrieval=object()).generate_subtitles(
        CamCatState(
            **state,
            domain_commands=result["domain_commands"],
            simulated_document=result["simulated_document"],
        )
    )
    assert not [
        t
        for t in output["simulated_document"]["timeline"]["tracks"]
        if t["type"] == "text" and t["segments"]
    ]
