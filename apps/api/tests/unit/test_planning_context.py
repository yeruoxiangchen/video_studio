from copy import deepcopy

from camcat.agent.context import (
    compact_materials,
    planning_snapshot,
    prompt_schema,
    source_duration_limits,
)
from camcat.agent.contract import command_proposal_schema
from camcat.domain.project import Canvas, EditingProjectV2, MediaSourceRef


def test_compaction_keeps_all_files_full_ranges_and_scoped_visual_evidence():
    media = [
        {
            "media_id": str(i),
            "filename": f"{i}.mp4",
            "duration_seconds": 10,
            "visual_semantics": {
                "description": f"visual content {i}",
                "subject_view": "back",
                "camera_motion": "push in",
                "temporal_evidence": [
                    "Beginning: person walks toward camera",
                    "End: person stands still",
                ],
            },
            "visual_evidence": {
                "media_id": str(i),
                "scope": "whole_media",
                "start_time": 0,
                "end_time": 10,
            },
        }
        for i in range(13)
    ]
    project = EditingProjectV2(
        project_id="p",
        title="t",
        goal="g",
        canvas=Canvas(width=1280, height=720, fps_num=30, fps_den=1),
        sources=[
            MediaSourceRef(
                source_id=m["media_id"],
                origin="user_upload",
                storage_key="private/key",
                retention_class="transient_4h",
                metadata=m,
            )
            for m in media
        ],
        metadata={"source_media": media},
    )
    materials = [
        {
            "source_id": str(i),
            "origin": "user_upload",
            "media_type": "video",
            "source_start_us": j * 1_000_000,
            "source_duration_us": 1_000_000,
            "description": "repeated metadata" * 100,
        }
        for i in range(12)
        for j in range(4)
    ]
    before = deepcopy(materials)
    result = compact_materials(materials, project)
    assert len(result) == 13
    assert {r["filename"] for r in result} == {m["filename"] for m in media}
    assert all(r["source_start_us"] == 0 and r["source_duration_us"] == 10_000_000 for r in result)
    assert result[0]["description"] == "visual content 0"
    assert result[0]["shot"] == {"subject_view": "back", "camera_motion": "push in"}
    assert result[0]["temporal_evidence"] == media[0]["visual_semantics"]["temporal_evidence"]
    assert result[0]["visual_evidence"]["scope"] == "whole_media"
    assert "storage_key" not in planning_snapshot(project)["sources"][0]
    assert source_duration_limits(project, materials)["0"] == 10_000_000
    assert materials == before


def test_schema_compaction_keeps_title_command_properties_and_validation_rules():
    original = command_proposal_schema()
    schema = prompt_schema(original)
    assert "title" in schema["$defs"]["UpdateTitle"]["properties"]
    assert (
        schema["$defs"]["UpdateTitle"]["required"] == original["$defs"]["UpdateTitle"]["required"]
    )
    assert schema["$defs"]["VideoSegment"]["properties"]["source_start_us"]["minimum"] == 0
