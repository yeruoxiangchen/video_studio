from copy import deepcopy

import pytest
from camcat.agent.contract import CommandProposal, command_proposal_schema
from pydantic import ValidationError


@pytest.mark.parametrize("inline", [False, True])
def test_proposal_derives_video_duration_and_rejects_zero_source_length(inline: bool) -> None:
    segment = {
        "type": "video",
        "segment_id": "cut",
        "source_id": "uploaded",
        "timeline_start_us": 0,
        "source_start_us": 500_000,
        "source_duration_us": 1_000_000,
        "speed": 1.25,
    }
    command = (
        {
            "type": "add_track",
            "track": {"type": "video", "track_id": "main", "name": "Main", "segments": [segment]},
        }
        if inline
        else {"type": "insert_segment", "track_id": "main", "segment": segment}
    )
    proposal = {"commands": [command]}
    parsed = CommandProposal.model_validate(proposal)
    materialized = parsed.commands[0].track.segments[0] if inline else parsed.commands[0].segment
    assert materialized.source_start_us == 500_000
    assert materialized.timeline_duration_us == 800_000
    assert "timeline_duration_us" not in segment
    bad = deepcopy(proposal)
    bad_command = bad["commands"][0]
    bad_segment = bad_command["track"]["segments"][0] if inline else bad_command["segment"]
    bad_segment["source_duration_us"] = 0
    with pytest.raises(ValidationError):
        CommandProposal.model_validate(bad)


def test_agent_schema_requires_discriminators_and_excludes_server_owned_commands() -> None:
    schema = command_proposal_schema()
    mapping = schema["properties"]["commands"]["items"]["discriminator"]["mapping"]
    assert {"insert_segment", "add_track", "update_text_segment"} <= set(mapping)
    assert not {"initialize_project", "register_source", "update_canvas"} & set(mapping)
    assert "timeline_duration_us" not in schema["$defs"]["VideoSegment"]["properties"]
    assert "timeline_duration_us" in schema["$defs"]["AudioSegment"]["required"]
    for definition in schema["$defs"].values():
        if "const" in definition.get("properties", {}).get("type", {}):
            assert "type" in definition["required"]
