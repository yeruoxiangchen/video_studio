"""Pure planning-loop regression; scripted proposals are unit inputs only."""

import json
from copy import deepcopy

import pytest
from camcat.agent.graph import CamCatGraph, CamCatState, EditPlanRejected
from camcat.domain.project import (
    AudioSegment,
    AudioTrack,
    Canvas,
    EditingProjectV2,
    MediaSourceRef,
    TextSegment,
    TextTrack,
    VideoSegment,
    VideoTrack,
)


def test_source_audio_partial_trim_follows_its_matching_video_range():
    class SourceAudioProposals(Proposals):
        def json_completion(self, *, system, user, response_schema=None):
            if response_schema and "terms" in response_schema.get("properties", {}):
                return {"terms": ["Camcat"]}
            self.requests.append(json.loads(user))
            return {
                "requires_full_plan": False,
                "commands": [
                    {
                        "type": "trim_segment",
                        "segment_id": "v",
                        "source_start_us": 0,
                        "source_duration_us": 2_500_000,
                    },
                    {
                        "type": "update_audio_segment",
                        "segment_id": "a",
                        "source_duration_us": 2_500_000,
                    },
                    {"type": "update_text_segment", "segment_id": "c", "text": "Hi Camcat!"},
                ],
            }

    state = revision_state()
    audio = state["current_document"]["timeline"]["tracks"][1]
    audio["role"] = "dialogue"
    audio["segments"][0]["source_id"] = "footage"
    original = deepcopy(state)
    model = SourceAudioProposals(repaired=False)
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(state)
    after = EditingProjectV2.model_validate(result["simulated_document"])
    audio = after.timeline.tracks[1].segments[0]
    assert audio.source_duration_us == audio.timeline_duration_us == 2_500_000
    assert len(model.requests) == 1
    assert state == original


def revision_state():
    project = EditingProjectV2(
        project_id="timing-repair",
        title="Timing repair",
        goal="A short video",
        canvas=Canvas(width=1080, height=1920, fps_num=30, fps_den=1),
        sources=[
            MediaSourceRef(
                source_id="footage",
                origin="user_upload",
                storage_key="temporary/video",
                retention_class="transient_4h",
            ),
            MediaSourceRef(
                source_id="music",
                origin="licensed_library",
                storage_key="library/music",
                retention_class="library",
                metadata={"media_type": "audio", "duration_us": 4_000_000},
            ),
        ],
        metadata={
            "source_media": [
                {"media_id": "footage", "filename": "source.mp4", "duration_seconds": 4}
            ]
        },
    )
    project.timeline.tracks.extend(
        [
            VideoTrack(
                track_id="video",
                name="Video",
                segments=[
                    VideoSegment(
                        segment_id="v",
                        source_id="footage",
                        timeline_start_us=0,
                        timeline_duration_us=4_000_000,
                        source_start_us=0,
                        source_duration_us=4_000_000,
                    )
                ],
            ),
            AudioTrack(
                track_id="audio",
                name="Music",
                role="bgm",
                segments=[
                    AudioSegment(
                        segment_id="a",
                        source_id="music",
                        timeline_start_us=0,
                        timeline_duration_us=4_000_000,
                        source_start_us=0,
                        source_duration_us=4_000_000,
                    )
                ],
            ),
            TextTrack(
                track_id="captions",
                name="Captions",
                segments=[
                    TextSegment(
                        segment_id="c", start_us=0, duration_us=1_000_000, text="Welcome to Camcat!"
                    )
                ],
            ),
        ]
    )
    return CamCatState(
        mode="edit",
        query_text="节奏更紧凑，字幕更短",
        current_document=project.model_dump(mode="json", by_alias=True),
        source_materials=[],
        ranked_materials=[],
        intent={
            "operation_scope": "refine",
            "caption_operation": "shorten",
            "external_material_ratio_limit": 0,
            "video_transform_requested": False,
        },
    )


class Proposals:
    def __init__(self, repaired):
        self.repaired, self.requests = repaired, []

    def json_completion(self, *, system, user, response_schema=None):
        if response_schema and "terms" in response_schema.get("properties", {}):
            return {"terms": ["Camcat"]}
        self.requests.append(json.loads(user))
        if len(self.requests) == 1:
            return {"commands": [], "requires_full_plan": False}
        commands = [
            {
                "type": "trim_segment",
                "segment_id": "v",
                "source_start_us": 0,
                "source_duration_us": 2_500_000,
            },
            {"type": "update_text_segment", "segment_id": "c", "text": "Hi Camcat!"},
        ]
        if len(self.requests) == 3 and self.repaired:
            commands.append(
                {
                    "type": "update_audio_segment",
                    "segment_id": "a",
                    "timeline_duration_us": 2_500_000,
                    "source_duration_us": 2_500_000,
                }
            )
        return {"commands": commands, "requires_full_plan": False}


def test_a_refinement_can_repair_audio_after_an_earlier_invalid_compact_proposal():
    model = Proposals(repaired=True)
    state = revision_state()
    original = deepcopy(state)
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(state)
    assert len(model.requests) == 2
    assert any(command["type"] == "update_audio_segment" for command in result["domain_commands"])
    after = EditingProjectV2.model_validate(result["simulated_document"])
    assert after.timeline.tracks[0].segments[0].timeline_duration_us == 2_500_000
    assert after.timeline.tracks[1].segments[0].timeline_duration_us == 2_500_000
    assert after.timeline.tracks[2].segments[0].text == "Hi Camcat!"
    assert state == original


def test_invalid_dialogue_timing_still_stops_at_the_existing_repair_ceiling():
    model = Proposals(repaired=False)
    state = revision_state()
    original = deepcopy(state)
    state["current_document"]["timeline"]["tracks"][1]["role"] = "dialogue"
    original = deepcopy(state)
    with pytest.raises(EditPlanRejected):
        CamCatGraph(llm=model, retrieval=object()).generate_commands(state)
    assert len(model.requests) == 2
    assert state == original


def test_name_protection_gets_one_bounded_text_repair_after_an_invalid_plan():
    class NameDroppingProposals(Proposals):
        def json_completion(self, *, system, user, response_schema=None):
            if response_schema and "text" in response_schema.get("properties", {}):
                return {"text": "Hi Camcat!"}
            result = super().json_completion(
                system=system, user=user, response_schema=response_schema
            )
            for command in result.get("commands", []):
                if command["type"] == "update_text_segment":
                    command["text"] = "Welcome!"
            return result

    model = NameDroppingProposals(repaired=False)
    result = CamCatGraph(llm=model, retrieval=object()).generate_commands(revision_state())
    assert len(model.requests) == 3
    after = EditingProjectV2.model_validate(result["simulated_document"])
    assert after.timeline.tracks[2].segments[0].text == "Hi Camcat!"
    assert after.timeline.tracks[1].segments[0].timeline_duration_us == 2_500_000
