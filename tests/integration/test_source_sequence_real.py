"""Real-provider contract for filename selection and native full-original commands."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from camcat.agent.graph import CamCatGraph, CamCatState
from camcat.config import get_settings
from camcat.domain.project import Canvas, EditingProjectV2, MediaSourceRef
from camcat.media.ffmpeg import probe
from camcat.services.providers import QwenChatClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("CAMCAT_LOCAL_MODELS_EXTERNAL") != "1",
        reason="the configured real local Qwen provider is required",
    ),
]


def test_real_provider_maps_filenames_to_native_source_ids_and_exact_original_ranges():
    source = Path(os.environ["CAMCAT_EXTERNAL_TEST_VIDEO"])
    measured = probe(source)
    source_ids = [str(uuid4()) for _ in range(3)]
    # These logical uploads share measured media facts. This contract exercises
    # filename references, with no invented visual descriptions or VQA score.
    media = [
        {"media_id": source_id, "filename": name, "duration_seconds": measured.duration}
        for source_id, name in zip(
            source_ids, ("arrival.mp4", "bridge.mp4", "closing.mp4"), strict=True
        )
    ]
    project = EditingProjectV2(
        project_id=str(uuid4()),
        goal="Filename reference contract",
        title="Filename reference contract",
        canvas=Canvas(width=1280, height=720, fps_num=30, fps_den=1),
        sources=[
            MediaSourceRef(
                source_id=item["media_id"],
                origin="user_upload",
                storage_key="contract/" + source.name,
                retention_class="transient_4h",
                metadata=item,
            )
            for item in media
        ],
        metadata={"source_media": media},
    )
    state = CamCatState(
        query_text=(
            "Join closing.mp4 FIRST, then arrival.mp4 SECOND, then bridge.mp4 LAST. "
            "Use all clips, each clip in full at original speed. "
            "No captions, music or transitions. 外部素材0%。"
        ),
        intent={
            "planning_mode": "source_sequence",
            "aspect_ratio": "16:9",
            "external_material_ratio_limit": 0,
        },
        current_document=project.model_dump(mode="json", by_alias=True),
        source_materials=[],
        ranked_materials=[],
    )
    graph = CamCatGraph(llm=QwenChatClient(get_settings()), retrieval=object())
    result = graph.generate_commands(state)
    segments = result["simulated_document"]["timeline"]["tracks"][0]["segments"]
    expected_duration = round(measured.duration * 1_000_000)
    assert [segment["source_id"] for segment in segments] == [
        source_ids[2],
        source_ids[0],
        source_ids[1],
    ]
    assert [segment["timeline_start_us"] for segment in segments] == [
        0,
        expected_duration,
        expected_duration * 2,
    ]
    assert all(
        segment["source_start_us"] == 0
        and segment["source_duration_us"] == expected_duration
        and segment["timeline_duration_us"] == expected_duration
        and segment["speed"] == 1
        and segment["reason"]
        for segment in segments
    )
