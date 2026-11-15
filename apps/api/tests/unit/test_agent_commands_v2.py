from __future__ import annotations

import json
from copy import deepcopy

import pytest
from camcat.agent.graph import CamCatGraph, CamCatState, _planning_repair_context
from camcat.domain.commands import DomainEditCommand, TrimSegment, UpdateTextSegment
from camcat.domain.project import Canvas, EditingProjectV2, MediaSourceRef
from camcat.domain.reducer import reduce_commands
from pydantic import ValidationError


class CommandLlm:
    def json_completion(self, *, system: str, user: str, response_schema=None):
        assert "RFC6902" in user
        return {
            "commands": [
                {
                    "type": "add_track",
                    "track": {
                        "type": "video",
                        "track_id": "main",
                        "name": "Main",
                        "segments": [],
                    },
                },
                {
                    "type": "insert_segment",
                    "track_id": "main",
                    "segment": {
                        "type": "video",
                        "segment_id": "segment-1",
                        "source_id": "source-1",
                        "timeline_start_us": 0,
                        "timeline_duration_us": 1_000_000,
                        "source_start_us": 0,
                        "source_duration_us": 1_000_000,
                    },
                },
            ],
            "summary": "typed commands",
        }


def _planning_state() -> CamCatState:
    project = EditingProjectV2(
        project_id="p",
        goal="g",
        title="t",
        canvas=Canvas(width=1920, height=1080, fps_num=30, fps_den=1),
        sources=[
            MediaSourceRef(
                source_id="source-1",
                origin="user_upload",
                storage_key="temporary/source.mp4",
                retention_class="transient_4h",
            )
        ],
    )
    return CamCatState(
        query_text="make it concise",
        intent={"aspect_ratio": "16:9", "external_material_ratio_limit": 0.25},
        current_document=project.model_dump(mode="json", by_alias=True),
        source_materials=[
            {
                "media_id": "source-1",
                "start_time": 0,
                "end_time": 1,
                "description_text": "user footage",
            }
        ],
        ranked_materials=[],
    )


def test_missing_user_original_has_a_typed_input_failure() -> None:
    from camcat.agent.errors import AgentInputError

    state = _planning_state()
    state["source_materials"] = []
    state["current_document"]["sources"] = []
    graph = CamCatGraph(llm=object(), retrieval=object())  # type: ignore[arg-type]
    with pytest.raises(AgentInputError) as error:
        graph.generate_commands(state)
    assert error.value.error_code == "source_required"


def test_agent_generates_simulates_and_validates_only_typed_delta_commands() -> None:
    graph = CamCatGraph(llm=CommandLlm(), retrieval=object())  # type: ignore[arg-type]
    state = _planning_state()
    planned = graph.generate_commands(state)
    command_types = [item["type"] for item in planned["domain_commands"]]
    assert command_types == ["update_canvas", "add_track", "insert_segment"]
    assert "operations" not in planned
    with_subtitles = graph.generate_subtitles(
        CamCatState(
            **state,
            domain_commands=planned["domain_commands"],
            simulated_document=planned["simulated_document"],
        )
    )
    validated = graph.validate_project(
        CamCatState(
            **state,
            domain_commands=with_subtitles["domain_commands"],
            simulated_document=with_subtitles["simulated_document"],
        )
    )
    assert validated["simulated_document"]["schema"] == "camcat-editing-project/v2"


def test_picture_and_audio_preservation_survive_a_later_subtitle_only_edit() -> None:
    state = _planning_state()
    state["query_text"] = "Retain the picture and original audio."
    graph = CamCatGraph(llm=CommandLlm(), retrieval=object())  # type: ignore[arg-type]
    initial = graph.generate_commands(state)
    assert initial["simulated_document"]["metadata"]["render_policy"] == {
        "preserve_color": True,
        "preserve_audio": True,
    }
    state["current_document"] = initial["simulated_document"]
    state["current_document"]["timeline"]["tracks"].append(
        {
            "type": "text",
            "track_id": "captions",
            "name": "captions",
            "segments": [
                {
                    "type": "text",
                    "segment_id": "c1",
                    "text": "Hello",
                    "start_us": 0,
                    "duration_us": 1_000_000,
                }
            ],
        }
    )
    state["query_text"] = "只把字幕改为 Hi"
    # A model's unspecified/default false must not clear a saved user preference.
    state["intent"]["delivery_constraints"] = {"preserve_color": False, "preserve_audio": False}

    class CaptionModel:
        def json_completion(self, **_kwargs):
            return {"commands": [{"type": "update_text_segment", "segment_id": "c1", "text": "Hi"}]}

    followup = CamCatGraph(llm=CaptionModel(), retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    assert (
        followup["simulated_document"]["metadata"]["render_policy"]
        == initial["simulated_document"]["metadata"]["render_policy"]
    )
    assert [c["type"] for c in followup["domain_commands"]] == ["update_text_segment"]


class SemanticRepairLlm(CommandLlm):
    """Unit-only scripted proposals exercise reducer feedback, without a provider claim."""

    def __init__(self, *, repair: bool = True):
        self.repair = repair
        self.requests = []

    def json_completion(self, *, system: str, user: str, response_schema=None):
        self.requests.append(json.loads(user))
        result = super().json_completion(system=system, user=user, response_schema=response_schema)
        if len(self.requests) == 1 or not self.repair:
            result["commands"].append(
                {
                    "type": "add_audio_segment",
                    "track_id": "audio:bgm",
                    "segment": {
                        "type": "audio",
                        "segment_id": "bgm-1",
                        "source_id": "source-1",
                        "timeline_start_us": 0,
                        "timeline_duration_us": 1_000_000,
                        "source_start_us": 0,
                        "source_duration_us": 1_000_000,
                    },
                }
            )
        return result


def test_semantic_command_error_is_returned_for_repair_without_mutating_the_project() -> None:
    model = SemanticRepairLlm()
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    state = _planning_state()
    original = deepcopy(state)
    planned = graph.generate_commands(state)
    assert len(model.requests) == 2
    assert "track is missing or ambiguous: audio:bgm" in model.requests[1]["validation_error"]
    assert model.requests[1]["previous_output"]["commands"][-1]["type"] == "add_audio_segment"
    assert all(item["type"] != "add_audio_segment" for item in planned["domain_commands"])
    assert state == original


def test_planning_excludes_repeated_analysis_metadata_from_the_model_context() -> None:
    model = SemanticRepairLlm()
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    state = _planning_state()
    state["current_document"]["metadata"]["source_segments"] = [
        {"segment_id": f"shot-{index}", "description_text": "long visual analysis " * 200}
        for index in range(20)
    ]
    original = deepcopy(state)
    graph.generate_commands(state)
    for request in model.requests:
        assert "metadata" not in request["project"]
        assert request["project"]["timeline"] == original["current_document"]["timeline"]
        assert request["materials"][0]["source_id"] == "source-1"
    assert state == original


def test_repeated_semantic_errors_stop_after_one_repair_without_mutating_the_project() -> None:
    from camcat.agent.errors import EditPlanRejected

    model = SemanticRepairLlm(repair=False)
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    state = _planning_state()
    original = deepcopy(state)
    with pytest.raises(EditPlanRejected, match="after repair:.*audio:bgm") as error:
        graph.generate_commands(state)
    assert error.value.error_code == "edit_plan_invalid"
    assert "audio:bgm" in error.value.details["validation_error"]
    assert len(model.requests) == 2
    assert state == original


def test_validation_feedback_keeps_the_domain_reason_without_model_inputs() -> None:
    from camcat.agent.graph import _validation_feedback

    document = _planning_state()["current_document"]
    document["sources"][0]["metadata"]["private_media_text"] = "private-transcript-content"
    document["sources"].append(document["sources"][0])
    with pytest.raises(ValidationError) as invalid:
        EditingProjectV2.model_validate(document)
    feedback = _validation_feedback(invalid.value)
    assert "source_id must be globally unique" in feedback
    assert "\n" not in feedback
    assert "input_value" not in feedback
    assert "private-transcript-content" not in feedback


class MusicCommandLlm(CommandLlm):
    def __init__(self, mode: str, source_id: str = "licensed-music"):
        self.mode = mode
        self.source_id = source_id
        self.requests = []

    def json_completion(self, *, system: str, user: str, response_schema=None):
        self.requests.append(json.loads(user))
        result = super().json_completion(system=system, user=user, response_schema=response_schema)
        segment = {
            "type": "audio",
            "segment_id": "music-1",
            "source_id": self.source_id,
            "timeline_start_us": 0,
            "timeline_duration_us": 1_000_000,
            "source_start_us": 0,
            "source_duration_us": 1_000_000,
            "volume": 0.15,
        }
        result["commands"].append(
            {
                "type": "add_track",
                "track": {
                    "type": "audio",
                    "track_id": "bgm",
                    "name": "BGM",
                    "role": "bgm",
                    "segments": [segment] if self.mode == "add_track" else [],
                },
            }
        )
        if self.mode != "add_track":
            result["commands"].append(
                {
                    "type": self.mode,
                    "track_id": "bgm",
                    "segment": segment,
                }
            )
        return result


def _state_with_audio_catalog() -> CamCatState:
    state = _planning_state()
    state["current_document"]["metadata"]["audio_library"] = [
        {
            "id": "licensed-music",
            "kind": "bgm",
            "title": "Licensed music",
            "storage_key": "library/music.mp3",
            "license_name": "CC0-1.0",
            "source_url": "https://example.com/licensed-music",
        },
        {
            "id": "unused-sfx",
            "kind": "sfx",
            "title": "Unused sound",
            "storage_key": "library/sfx.mp3",
            "license_name": "CC0-1.0",
            "source_url": "https://example.com/licensed-sfx",
        },
    ]
    return state


@pytest.mark.parametrize("mode", ["add_audio_segment", "insert_segment", "add_track"])
def test_agent_registers_referenced_licensed_audio_before_applying_commands(mode: str) -> None:
    model = MusicCommandLlm(mode)
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    state = _state_with_audio_catalog()
    original = deepcopy(state)
    planned = graph.generate_commands(state)
    registrations = [
        item["source"] for item in planned["domain_commands"] if item["type"] == "register_source"
    ]
    assert [item["source_id"] for item in registrations] == ["licensed-music"]
    assert registrations[0]["storage_key"] == "library/music.mp3"
    assert registrations[0]["metadata"]["license_name"] == "CC0-1.0"
    assert registrations[0]["metadata"]["source_url"] == "https://example.com/licensed-music"
    assert (
        planned["simulated_document"]["timeline"]["tracks"][1]["segments"][0]["source_id"]
        == "licensed-music"
    )
    assert state == original


def test_agent_rejects_audio_outside_the_supplied_catalog() -> None:
    model = MusicCommandLlm("add_audio_segment", source_id="invented-music")
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    state = _state_with_audio_catalog()
    with pytest.raises(ValueError, match="source outside the supplied material set"):
        graph.generate_commands(state)


def test_planner_receives_measured_audio_source_duration_and_registers_it() -> None:
    model = MusicCommandLlm("add_audio_segment")
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    state = _state_with_audio_catalog()
    state["current_document"]["metadata"]["audio_library"][0]["duration_us"] = 2_000_000
    planned = graph.generate_commands(state)
    music = next(
        item for item in model.requests[0]["materials"] if item["source_id"] == "licensed-music"
    )
    assert music["source_start_us"] == 0
    assert music["source_duration_us"] == 2_000_000
    source = next(
        item
        for item in planned["simulated_document"]["sources"]
        if item["source_id"] == "licensed-music"
    )
    assert source["metadata"]["duration_us"] == 2_000_000


class TransitionRepairLlm(CommandLlm):
    def __init__(self):
        self.requests = []

    def json_completion(self, *, system: str, user: str, response_schema=None):
        payload = json.loads(user)
        self.requests.append(payload)
        result = super().json_completion(system=system, user=user, response_schema=response_schema)
        first = result["commands"][1]["segment"]
        first["source_duration_us"] = first["timeline_duration_us"] = 500_000
        second = {
            **first,
            "segment_id": "segment-2",
            "source_start_us": 500_000,
            "timeline_start_us": 500_000,
        }
        result["commands"].append({"type": "insert_segment", "track_id": "main", "segment": second})
        left, right = "segment-2", "segment-1"
        if len(self.requests) > 1:
            context = payload["repair_context"]
            pair = context["allowed_transitions"][0]
            assert pair["left_segment_id"] == "segment-1"
            assert pair["right_segment_id"] == "segment-2"
            assert pair["max_duration_us"] == 499_999
            left, right = pair["left_segment_id"], pair["right_segment_id"]
        result["commands"].append(
            {
                "type": "set_transition",
                "transition_id": "fade-1",
                "left_segment_id": left,
                "right_segment_id": right,
                "transition_type": "dissolve",
                "duration_us": 100_000,
            }
        )
        return result


def test_transition_repair_receives_actual_simulated_adjacency_and_duration_limits() -> None:
    model = TransitionRepairLlm()
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    state = _planning_state()
    original = deepcopy(state)
    planned = graph.generate_commands(state)
    assert len(model.requests) == 2
    context = model.requests[1]["repair_context"]
    assert context["failed_command_type"] == "set_transition"
    assert [item["segment_id"] for item in context["tracks"][0]["segments"]] == [
        "segment-1",
        "segment-2",
    ]
    transition = planned["simulated_document"]["timeline"]["transitions"][0]
    assert (transition["left_segment_id"], transition["right_segment_id"]) == (
        "segment-1",
        "segment-2",
    )
    assert state == original


@pytest.mark.parametrize("valid_recovery", [True, False])
def test_repeated_initial_transition_failure_has_one_model_selected_primary_recovery(
    valid_recovery: bool,
) -> None:
    class RepeatedTransitionModel:
        def __init__(self):
            self.calls = 0

        def json_completion(self, *, system: str, user: str, response_schema=None):
            self.calls += 1
            if self.calls <= 2:
                return TransitionRepairLlm().json_completion(
                    system=system, user=user, response_schema=response_schema
                )
            assert self.calls == 3
            assert response_schema["properties"]["source_id"]["enum"] == ["source-1"]
            return {
                "source_id": "source-1" if valid_recovery else "invented",
                "source_start_us": 0,
                "source_duration_us": 1_000_000,
            }

    state = _planning_state()
    before = deepcopy(state)
    model = RepeatedTransitionModel()
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    if valid_recovery:
        result = graph.generate_commands(state)
        assert result["planning_recovery"] == "primary_after_plan_validation_failure"
        assert not result["simulated_document"]["timeline"]["transitions"]
        assert (
            result["simulated_document"]["timeline"]["tracks"][0]["segments"][0]["source_id"]
            == "source-1"
        )
    else:
        with pytest.raises(ValueError, match="failed validation after repair"):
            graph.generate_commands(state)
    assert model.calls == 3
    assert state == before


def test_initial_recovery_handles_different_validation_errors_across_attempts() -> None:
    class MixedFailureModel:
        def __init__(self):
            self.calls = 0

        def json_completion(self, *, system: str, user: str, response_schema=None):
            self.calls += 1
            if self.calls == 1:
                return TransitionRepairLlm().json_completion(
                    system=system, user=user, response_schema=response_schema
                )
            if self.calls == 2:
                result = CommandLlm().json_completion(
                    system=system, user=user, response_schema=response_schema
                )
                result["commands"].append(
                    {
                        "type": "add_track",
                        "track": {
                            "type": "text",
                            "track_id": "captions",
                            "name": "Captions",
                            "segments": [
                                {
                                    "type": "text",
                                    "segment_id": "too-long",
                                    "start_us": 0,
                                    "duration_us": 2_000_000,
                                    "text": "Overruns the one-second video",
                                }
                            ],
                        },
                    }
                )
                return result
            assert self.calls == 3
            return {"source_id": "source-1", "source_start_us": 0, "source_duration_us": 1_000_000}

    model = MixedFailureModel()
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(_planning_state())  # type: ignore[arg-type]
    assert model.calls == 3
    assert not result["simulated_document"]["timeline"]["transitions"]


class SourceMixRepairLlm(CommandLlm):
    def __init__(self, empty_prefix: bool = False):
        self.requests = []
        self.empty_prefix = empty_prefix

    def json_completion(self, *, system: str, user: str, response_schema=None):
        payload = json.loads(user)
        self.requests.append(payload)
        if len(self.requests) == 1:
            result = super().json_completion(
                system=system, user=user, response_schema=response_schema
            )
            if self.empty_prefix:
                result["commands"][1] = {
                    "type": "add_audio_segment",
                    "track_id": "missing-audio",
                    "segment": {
                        "type": "audio",
                        "segment_id": "audio-1",
                        "source_id": "source-1",
                        "timeline_start_us": 0,
                        "timeline_duration_us": 1_000_000,
                        "source_start_us": 0,
                        "source_duration_us": 1_000_000,
                    },
                }
            else:
                result["commands"][1]["segment"]["source_id"] = "library-clip"
            return result
        context = payload["repair_context"]
        assert context["video_duration_us_by_origin"] == {
            "user_upload": 0,
            "licensed_library": 0 if self.empty_prefix else 1_000_000,
        }
        if not self.empty_prefix:
            assert context["tracks"][0]["segments"][0]["source_id"] == "library-clip"
        assert response_schema["properties"]["source_id"]["enum"] == ["source-1"]
        return {
            "source_id": "source-1",
            "source_start_us": 800_000,
            "source_duration_us": 4_000_000,
        }


@pytest.mark.parametrize("empty_prefix", [False, True])
def test_reference_retrieval_plans_from_uploaded_video_and_repairs_source_mix(
    empty_prefix: bool,
) -> None:
    model = SourceMixRepairLlm(empty_prefix)
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    state = _planning_state()
    state["intent"].update(target_duration_seconds=4, aspect_ratio="9:16")
    state["ranked_materials"] = [
        {
            "segment_id": "library-clip",
            "reranker_score": 0.9,
            "entity": {
                "start_time": 0,
                "end_time": 4,
                "storage_key": "library/clip.mp4",
                "license_name": "CC0-1.0",
            },
        }
    ]
    original = deepcopy(state)
    planned = graph.generate_commands(state)
    command_types = model.requests[0]["contract"]["allowed_commands"]
    assert "insert_segment" in command_types
    assert "update_text_segment" not in command_types
    assert "remove_segment" not in command_types
    assert "add_audio_segment" in command_types
    assert model.requests[0]["contract"]["video_source_policy"] == {
        "primary_source_ids": ["source-1"],
        "external_material_ratio_limit": 0.25,
        "target_duration_us": 4_000_000,
        "max_library_video_us_at_target": 1_000_000,
    }
    reason = "track is missing" if empty_prefix else "user footage must remain the primary story"
    assert reason in model.requests[1]["validation_error"]
    clip = planned["simulated_document"]["timeline"]["tracks"][0]["segments"][0]
    assert clip["source_id"] == "source-1"
    assert clip["source_start_us"] == 0
    assert clip["source_duration_us"] == 1_000_000
    assert state == original


def test_initial_mixed_plan_over_library_ratio_repairs_with_uploaded_source_selection() -> None:
    class MixedSourceModel(CommandLlm):
        def __init__(self):
            self.requests = []

        def json_completion(self, *, system: str, user: str, response_schema=None):
            payload = json.loads(user)
            self.requests.append(payload)
            if len(self.requests) == 1:
                result = super().json_completion(system=system, user=user)
                result["commands"].append(
                    {
                        "type": "insert_segment",
                        "track_id": "main",
                        "segment": {
                            "type": "video",
                            "segment_id": "cutaway",
                            "source_id": "library-clip",
                            "timeline_start_us": 1_000_000,
                            "timeline_duration_us": 1_000_000,
                            "source_start_us": 0,
                            "source_duration_us": 1_000_000,
                        },
                    }
                )
                return result
            assert "source_id" in response_schema["properties"], (
                "Ratio repair must select from uploaded footage"
            )
            assert response_schema["properties"]["source_id"]["enum"] == ["source-1"]
            assert payload["repair_context"]["video_duration_us_by_origin"] == {
                "user_upload": 1_000_000,
                "licensed_library": 1_000_000,
            }
            return {"source_id": "source-1", "source_start_us": 0, "source_duration_us": 1_000_000}

    model = MixedSourceModel()
    state = _planning_state()
    state["ranked_materials"] = [
        {
            "segment_id": "library-clip",
            "reranker_score": 0.9,
            "entity": {
                "start_time": 0,
                "end_time": 4,
                "storage_key": "library/clip.mp4",
                "license_name": "CC0-1.0",
            },
        }
    ]
    before = deepcopy(state)
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    assert len(model.requests) == 2
    assert "licensed-library duration exceeds" in model.requests[1]["validation_error"]
    tracks = result["simulated_document"]["timeline"]["tracks"]
    assert [segment["source_id"] for track in tracks for segment in track["segments"]] == [
        "source-1"
    ]
    assert state == before


@pytest.mark.parametrize("trimmed", [False, True])
def test_followup_keeps_edited_caption_text_and_aligns_it_to_the_source_cut(trimmed: bool) -> None:
    graph = CamCatGraph(llm=CommandLlm(), retrieval=object())  # type: ignore[arg-type]
    state = _planning_state()
    state["current_document"]["metadata"]["source_segments"] = [
        {
            "media_id": "source-1",
            "start_time": 0,
            "transcript_cues": [{"start": 0.1, "end": 0.9, "text": "The original caption"}],
        }
    ]
    first = graph.generate_subtitles(CamCatState(**state, **graph.generate_commands(state)))
    document = first["simulated_document"]
    caption = document["timeline"]["tracks"][1]["segments"][0]
    commands: list[DomainEditCommand] = [
        UpdateTextSegment(segment_id=caption["segment_id"], text="Short caption")
    ]
    if trimmed:
        commands = [
            TrimSegment(segment_id="segment-1", source_start_us=0, source_duration_us=650_000),
            *commands,
        ]
    revised = reduce_commands(EditingProjectV2.model_validate(document), commands).project
    followup = graph.generate_subtitles(
        CamCatState(
            current_document=document,
            simulated_document=revised.model_dump(mode="json", by_alias=True),
            domain_commands=[command.model_dump(mode="json") for command in commands],
        )
    )
    captions = followup["simulated_document"]["timeline"]["tracks"][1]["segments"]
    assert len(captions) == 1
    assert captions[0]["text"] == "Short caption"
    assert captions[0]["start_us"] == 100_000
    assert captions[0]["duration_us"] == (550_000 if trimmed else 800_000)


@pytest.mark.parametrize("kind", ["audio", "text"])
def test_initial_tracks_outside_video_end_are_repaired_before_persistence(kind: str) -> None:
    class OutlastingTrackModel(CommandLlm):
        def __init__(self):
            self.calls = 0

        def json_completion(self, *, system: str, user: str, response_schema=None):
            self.calls += 1
            if self.calls == 1:
                result = super().json_completion(system=system, user=user)
                segment = (
                    {
                        "type": "audio",
                        "segment_id": "overlong-music",
                        "source_id": "licensed-music",
                        "timeline_start_us": 0,
                        "timeline_duration_us": 2_000_000,
                        "source_start_us": 0,
                        "source_duration_us": 2_000_000,
                    }
                    if kind == "audio"
                    else {
                        "type": "text",
                        "segment_id": "overlong-caption",
                        "text": "A caption",
                        "start_us": 0,
                        "duration_us": 2_000_000,
                    }
                )
                result["commands"].append(
                    {
                        "type": "add_track",
                        "track": {
                            "type": kind,
                            "track_id": "overlong",
                            "name": "Overlong",
                            "segments": [segment],
                            **({"role": "bgm"} if kind == "audio" else {}),
                        },
                    }
                )
                return result
            assert "source_id" in response_schema["properties"], (
                "Unrenderable initial tracks must trigger original-footage repair"
            )
            payload = json.loads(user)
            assert f"{kind} segment exceeds" in payload["validation_error"]
            assert payload["repair_context"]["visible_timeline_end_us"] == 1_000_000
            return {"source_id": "source-1", "source_start_us": 0, "source_duration_us": 1_000_000}

    model = OutlastingTrackModel()
    state = _state_with_audio_catalog()
    before = deepcopy(state)
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    assert model.calls == 2
    assert [track["type"] for track in result["simulated_document"]["timeline"]["tracks"]] == [
        "video"
    ]
    assert state == before


@pytest.mark.parametrize("failure", ["remove-original", "duplicate-segment"])
def test_followup_repair_preserves_uploaded_video_and_revises_existing_caption(
    failure: str,
) -> None:
    class PreserveStoryModel:
        def __init__(self):
            self.calls = 0

        def json_completion(self, *, system: str, user: str, response_schema=None):
            self.calls += 1
            if self.calls == 1:
                if failure == "remove-original":
                    return {"commands": [{"type": "remove_track", "track_id": "main"}]}
                return {
                    "commands": [
                        {
                            "type": "insert_segment",
                            "track_id": "main",
                            "segment": {
                                "type": "video",
                                "segment_id": "segment-1",
                                "source_id": "source-1",
                                "timeline_start_us": 0,
                                "source_start_us": 0,
                                "source_duration_us": 1_000_000,
                            },
                        }
                    ]
                }
            commands = response_schema["properties"]["commands"]["items"]["discriminator"][
                "mapping"
            ]
            assert not {
                "add_track",
                "insert_segment",
                "remove_track",
                "remove_segment",
                "replace_segment_media",
            }.intersection(commands)
            assert json.loads(user)["repair_context"]["visible_timeline_end_us"] == 1_000_000
            assert response_schema["$defs"]["UpdateTextSegment"]["properties"]["segment_id"][
                "enum"
            ] == ["caption"]
            return {
                "commands": [
                    {"type": "update_text_segment", "segment_id": "caption", "text": "Short."}
                ]
            }

    state = _planning_state()
    base = CamCatGraph(llm=CommandLlm(), retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    state["current_document"] = base["simulated_document"]
    state["current_document"]["timeline"]["tracks"].append(
        {
            "type": "text",
            "track_id": "captions",
            "name": "Captions",
            "segments": [
                {
                    "type": "text",
                    "segment_id": "caption",
                    "text": "A longer caption",
                    "start_us": 0,
                    "duration_us": 1_000_000,
                }
            ],
        }
    )
    before = deepcopy(state)
    model = PreserveStoryModel()
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    assert model.calls == 2
    tracks = result["simulated_document"]["timeline"]["tracks"]
    assert tracks[0]["segments"][0]["source_id"] == "source-1"
    assert tracks[1]["segments"][0]["text"] == "Short."
    assert state == before


@pytest.mark.parametrize("structural", [False, True])
def test_followup_starts_with_existing_segment_contract_and_can_escalate(structural: bool) -> None:
    state = _planning_state()
    base = CamCatGraph(llm=CommandLlm(), retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    state["current_document"] = base["simulated_document"]
    state["intent"]["operation_scope"] = "refine"
    before = deepcopy(state)

    class RevisionModel:
        def __init__(self):
            self.calls = 0

        def json_completion(self, *, system: str, user: str, response_schema=None):
            self.calls += 1
            mapping = response_schema["properties"]["commands"]["items"]["discriminator"]["mapping"]
            if self.calls == 1:
                assert "insert_segment" not in mapping
                assert response_schema["$defs"]["TrimSegment"]["properties"]["segment_id"][
                    "enum"
                ] == ["segment-1"]
                assert "requires_full_plan" in response_schema["properties"]
                if structural:
                    return {"requires_full_plan": True, "commands": []}
                return {
                    "commands": [
                        {
                            "type": "trim_segment",
                            "segment_id": "segment-1",
                            "source_start_us": 0,
                            "source_duration_us": 900_000,
                        }
                    ]
                }
            assert "insert_segment" in mapping
            return {
                "commands": [
                    {
                        "type": "insert_segment",
                        "track_id": "main",
                        "segment": {
                            "type": "video",
                            "segment_id": "second",
                            "source_id": "source-1",
                            "timeline_start_us": 1_000_000,
                            "source_start_us": 0,
                            "source_duration_us": 1_000_000,
                        },
                    }
                ]
            }

    model = RevisionModel()
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    assert model.calls == (2 if structural else 1)
    segments = result["simulated_document"]["timeline"]["tracks"][0]["segments"]
    assert len(segments) == (2 if structural else 1)
    if not structural:
        assert segments[0]["timeline_duration_us"] == 900_000
    assert state == before


@pytest.mark.parametrize("scope", ["structural", "unknown"])
def test_structural_or_unknown_scope_keeps_full_native_editing_capabilities(scope: str) -> None:
    state = _planning_state()
    base = CamCatGraph(llm=CommandLlm(), retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    state["current_document"] = base["simulated_document"]
    state["intent"]["operation_scope"] = scope

    class StructuralModel:
        def json_completion(self, *, system: str, user: str, response_schema=None):
            mapping = response_schema["properties"]["commands"]["items"]["discriminator"]["mapping"]
            assert {"split_segment", "insert_segment", "move_segment"} <= set(mapping)
            assert "requires_full_plan" not in response_schema["properties"]
            return {
                "commands": [
                    {
                        "type": "insert_segment",
                        "track_id": "main",
                        "segment": {
                            "type": "video",
                            "segment_id": "second",
                            "source_id": "source-1",
                            "timeline_start_us": 1_000_000,
                            "source_start_us": 0,
                            "source_duration_us": 1_000_000,
                        },
                    }
                ]
            }

    result = CamCatGraph(llm=StructuralModel(), retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    assert len(result["simulated_document"]["timeline"]["tracks"][0]["segments"]) == 2


@pytest.mark.parametrize("escalated", [False, True])
def test_structural_repair_retains_the_commands_needed_for_the_requested_operation(escalated):
    state = _planning_state()
    state["current_document"] = CamCatGraph(llm=CommandLlm(), retrieval=object()).generate_commands(
        state
    )["simulated_document"]
    state["intent"]["operation_scope"] = "refine" if escalated else "structural"
    state["query_text"] = "Split the current video clip at its midpoint."
    before = deepcopy(state)

    class SplitRepairModel:
        def __init__(self):
            self.calls = 0

        def json_completion(self, *, system, user, response_schema=None):
            self.calls += 1
            if escalated and self.calls == 1:
                return {"commands": [], "requires_full_plan": True}
            attempt = self.calls - int(escalated)
            mapping = response_schema["properties"]["commands"]["items"]["discriminator"]["mapping"]
            assert "split_segment" in mapping
            return {
                "commands": [
                    {
                        "type": "split_segment",
                        "segment_id": "segment-1",
                        "at_source_us": 2_000_000 if attempt == 1 else 500_000,
                        "right_segment_id": "right",
                    }
                ]
            }

    model = SplitRepairModel()
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(state)
    segments = result["simulated_document"]["timeline"]["tracks"][0]["segments"]
    assert [s["source_duration_us"] for s in segments] == [500_000, 500_000]
    assert model.calls == 2 + int(escalated)
    assert state == before


def test_planning_context_exposes_absolute_source_midpoint_for_offset_speed_clip() -> None:
    state = _planning_state()
    result = CamCatGraph(llm=CommandLlm(), retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    document = result["simulated_document"]
    document["timeline"]["tracks"][0]["segments"][0].update(
        source_start_us=1_056_000,
        source_duration_us=4_000_000,
        timeline_duration_us=3_200_000,
        speed=1.25,
    )
    project = EditingProjectV2.model_validate(document)
    segment = _planning_repair_context(project, [])["tracks"][0]["segments"][0]
    assert segment["source_start_us"] == 1_056_000
    assert segment["source_duration_us"] == 4_000_000
    assert segment["midpoint_source_us"] == 3_056_000
    assert segment["speed"] == 1.25


def test_followup_trim_allows_source_captions_to_align_before_final_bounds_check() -> None:
    class TrimModel:
        def __init__(self):
            self.calls = 0

        def json_completion(self, **_kwargs):
            self.calls += 1
            return {
                "commands": [
                    {
                        "type": "trim_segment",
                        "segment_id": "segment-1",
                        "source_start_us": 0,
                        "source_duration_us": 650_000,
                    }
                ]
            }

    state = _planning_state()
    state["current_document"]["metadata"]["source_segments"] = [
        {
            "media_id": "source-1",
            "start_time": 0,
            "transcript_cues": [{"start": 0.1, "end": 0.9, "text": "Source caption"}],
        }
    ]
    preparer = CamCatGraph(llm=CommandLlm(), retrieval=object())  # type: ignore[arg-type]
    initial = preparer.generate_subtitles(CamCatState(**state, **preparer.generate_commands(state)))
    state["current_document"] = initial["simulated_document"]
    model = TrimModel()
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    commands = graph.generate_commands(state)
    final = graph.generate_subtitles(CamCatState(**state, **commands))
    graph.validate_project(CamCatState(**state, **final))
    assert model.calls == 1
    caption = final["simulated_document"]["timeline"]["tracks"][1]["segments"][0]
    assert caption["start_us"] + caption["duration_us"] == 650_000


@pytest.mark.parametrize("width,height", [(720, 1280), (1280, 720)])
def test_subtitle_revision_preserves_existing_canvas_exactly(width: int, height: int) -> None:
    class CaptionModel:
        def json_completion(self, **_kwargs):
            return {"commands": [{"type": "update_text_segment", "segment_id": "c1", "text": "Hi"}]}

    state = _planning_state()
    base = CamCatGraph(llm=CommandLlm(), retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    state["current_document"] = base["simulated_document"]
    state["current_document"]["canvas"].update(width=width, height=height)
    state["current_document"]["timeline"]["tracks"].append(
        {
            "type": "text",
            "track_id": "captions",
            "name": "Captions",
            "segments": [
                {
                    "type": "text",
                    "segment_id": "c1",
                    "text": "Hello",
                    "start_us": 0,
                    "duration_us": 1_000_000,
                }
            ],
        }
    )
    state["query_text"] = "只把字幕改为 Hi，保持其他设置"
    state["intent"]["operation_scope"] = "refine"
    before = deepcopy(state)
    graph = CamCatGraph(llm=CaptionModel(), retrieval=object())  # type: ignore[arg-type]
    planned = graph.generate_commands(state)
    final = graph.generate_subtitles(CamCatState(**state, **planned))
    assert final["simulated_document"]["canvas"] == before["current_document"]["canvas"]
    assert [command["type"] for command in final["domain_commands"]] == ["update_text_segment"]
    assert state == before


def test_understanding_preserves_current_ratio_when_original_footage_has_another_shape() -> None:
    class UnderstandingModel:
        def json_completion(self, **_kwargs):
            return {"search_query": "earth", "operation_scope": "structural"}

    state = _planning_state()
    base = CamCatGraph(llm=CommandLlm(), retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    state["current_document"] = base["simulated_document"]
    state["current_document"]["canvas"].update(width=720, height=1280)
    state["current_document"]["metadata"]["source_media"] = [{"width": 1280, "height": 720}]
    state["query_text"] = "从中点拆分，保留上一轮的画幅"
    graph = CamCatGraph(llm=UnderstandingModel(), retrieval=object())  # type: ignore[arg-type]
    assert graph.understand(state)["intent"]["aspect_ratio"] == "9:16"


def test_requirement_understanding_uses_a_typed_top_level_contract() -> None:
    class UnderstandingModel:
        def json_completion(self, *, system, user, response_schema=None):
            assert response_schema is not None
            assert response_schema["additionalProperties"] is False
            assert "search_query" in response_schema["required"]
            assert {"operation_scope", "planning_mode", "delivery_constraints"} <= set(
                response_schema["properties"]
            )
            return {"search_query": "flower"}

    result = CamCatGraph(llm=UnderstandingModel(), retrieval=object()).understand(_planning_state())
    assert result["intent"]["planning_mode"] == "commands"
    assert result["intent"]["delivery_constraints"]["subtitles"] == "auto"


def test_requirement_wrapper_is_rejected_instead_of_losing_its_fields() -> None:
    from camcat.services.providers import ProviderOutputError

    class WrappedModel:
        def json_completion(self, **kwargs):
            return {"default": {"search_query": "flower", "operation_scope": "refine"}}

    with pytest.raises(ProviderOutputError):
        CamCatGraph(llm=WrappedModel(), retrieval=object()).understand(_planning_state())


@pytest.mark.parametrize(
    "query,width,height",
    [
        ("改为9:16并缩短字幕", 1080, 1920),
        ("保持16:9并缩短字幕", 1280, 720),
        ("只把字幕改为“9:16竖屏”，其他设置保持不变", 1280, 720),
        ("不要改为9:16，只缩短字幕", 1280, 720),
    ],
)
def test_followup_changes_ratio_only_when_requested(query: str, width: int, height: int) -> None:
    class TrimModel:
        def json_completion(self, **_kwargs):
            return {
                "commands": [
                    {
                        "type": "trim_segment",
                        "segment_id": "segment-1",
                        "source_start_us": 0,
                        "source_duration_us": 900_000,
                    }
                ]
            }

    state = _planning_state()
    base = CamCatGraph(llm=CommandLlm(), retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    state["current_document"] = base["simulated_document"]
    state["current_document"]["canvas"].update(width=1280, height=720)
    state["query_text"] = query
    graph = CamCatGraph(llm=TrimModel(), retrieval=object())  # type: ignore[arg-type]
    canvas = graph.generate_commands(state)["simulated_document"]["canvas"]
    assert (canvas["width"], canvas["height"]) == (width, height)


def test_revision_of_silent_footage_does_not_create_an_empty_caption_track() -> None:
    state = _planning_state()
    graph = CamCatGraph(llm=CommandLlm(), retrieval=object())  # type: ignore[arg-type]
    planned = graph.generate_commands(state)
    final = graph.generate_subtitles(CamCatState(**state, **planned))
    assert (
        final["simulated_document"]["timeline"]["tracks"]
        == planned["simulated_document"]["timeline"]["tracks"]
    )
    assert final["domain_commands"] == planned["domain_commands"]


def test_failed_caption_text_correction_is_rejected_without_mutating_the_project() -> None:
    from camcat.agent.errors import EditPlanRejected

    state = _planning_state()
    base = CamCatGraph(llm=CommandLlm(), retrieval=object()).generate_commands(state)
    state["current_document"] = base["simulated_document"]
    state["current_document"]["timeline"]["tracks"].append(
        {
            "type": "text",
            "track_id": "captions",
            "name": "Captions",
            "segments": [
                {
                    "type": "text",
                    "segment_id": "caption",
                    "start_us": 0,
                    "duration_us": 500_000,
                    "text": "Welcome to the museum!",
                }
            ],
        }
    )
    state["query_text"] = "Shorten the existing caption without translating it."
    state["intent"].update(operation_scope="refine", caption_operation="shorten")
    original = deepcopy(state)

    class InvalidCaptionModel:
        def json_completion(self, *, response_schema=None, **_kwargs):
            if "terms" in response_schema["properties"]:
                return {"terms": []}
            if "commands" in response_schema["properties"]:
                return {
                    "commands": [
                        {
                            "type": "update_text_segment",
                            "segment_id": "caption",
                            "text": "欢迎来到博物馆！",
                        }
                    ]
                }
            return {"text": "欢迎来到博物馆！"}

    with pytest.raises(EditPlanRejected, match="caption correction"):
        CamCatGraph(llm=InvalidCaptionModel(), retrieval=object()).generate_commands(state)
    assert state == original


@pytest.mark.parametrize("requested", [False, True])
def test_initial_video_placement_uses_default_unless_requested(requested: bool) -> None:
    class OffsetModel(CommandLlm):
        def json_completion(self, **kwargs):
            result = super().json_completion(**kwargs)
            for command in result["commands"]:
                if command.get("type") == "add_track" and command["track"]["type"] == "video":
                    for segment in command["track"]["segments"]:
                        segment["transform"] = {"x": 0.5, "y": 0.5}
                elif command.get("type") == "insert_segment":
                    command["segment"]["transform"] = {"x": 0.5, "y": 0.5}
            return result

    state = _planning_state()
    state["intent"]["video_transform_requested"] = requested
    result = CamCatGraph(llm=OffsetModel(), retrieval=object()).generate_commands(state)
    segment = result["simulated_document"]["timeline"]["tracks"][0]["segments"][0]
    assert segment["transform"]["x"] == (0.5 if requested else 0)
    assert segment["transform"]["y"] == (0.5 if requested else 0)


@pytest.mark.parametrize("start,end", [(2, 4), (1.25, 3.75)])
@pytest.mark.parametrize("repair", [True, False])
def test_absolute_source_trim_repairs_wrong_start_before_accepting_the_plan(
    start: float,
    end: float,
    repair: bool,
) -> None:
    class SourceTrimModel:
        def __init__(self):
            self.calls = 0

        def json_completion(self, *, user: str, **_kwargs):
            self.calls += 1
            payload = json.loads(user)
            if self.calls == 2:
                assert "requested source range" in payload["validation_error"]
            return {
                "commands": [
                    {
                        "type": "trim_segment",
                        "segment_id": "segment-1",
                        "source_start_us": round(start * 1_000_000)
                        if repair and self.calls > 1
                        else 1_056_000,
                        "source_duration_us": round((end - start) * 1_000_000),
                    }
                ]
            }

    state = _planning_state()
    base = CamCatGraph(llm=CommandLlm(), retrieval=object()).generate_commands(state)  # type: ignore[arg-type]
    state["current_document"] = base["simulated_document"]
    state["current_document"]["timeline"]["tracks"][0]["segments"][0].update(
        source_start_us=1_056_000,
        source_duration_us=4_000_000,
        timeline_duration_us=4_000_000,
    )
    state["source_materials"][0]["end_time"] = 6
    state["query_text"] = f"将主视频裁剪为原片第{start}秒至第{end}秒，其他设置保持不变"
    before = deepcopy(state)
    model = SourceTrimModel()
    graph = CamCatGraph(llm=model, retrieval=object())  # type: ignore[arg-type]
    if repair:
        result = graph.generate_commands(state)
        clip = result["simulated_document"]["timeline"]["tracks"][0]["segments"][0]
        assert (clip["source_start_us"], clip["source_duration_us"]) == (
            round(start * 1_000_000),
            round((end - start) * 1_000_000),
        )
        assert result["planning_recovery"] == "source_range_after_plan_validation_failure"
    else:
        with pytest.raises(ValueError, match="requested source range"):
            graph.generate_commands(state)
    assert model.calls == 2
    assert state == before
