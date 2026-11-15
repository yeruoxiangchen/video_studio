"""Replay the actual failed structural edit with the configured local model."""

import json
import os
from pathlib import Path

import pytest
from camcat.agent.graph import CamCatGraph, CamCatState
from camcat.config import get_settings
from camcat.domain.project import EditingProjectV2, TextTrack, VideoTrack
from camcat.editing.policies import prepare_source_candidates
from camcat.services.providers import QwenChatClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("CAMCAT_LOCAL_MODELS_EXTERNAL") != "1"
        or not os.getenv("CAMCAT_SPLIT_REVISION_REGRESSION_STATE"),
        reason="requires the real local model and archived failed split revision",
    ),
]


def test_real_split_retains_the_existing_chinese_caption(tmp_path: Path):
    archived = json.loads(Path(os.environ["CAMCAT_SPLIT_REVISION_REGRESSION_STATE"]).read_text())
    project = EditingProjectV2.model_validate(archived["state"])
    instruction = (
        "把当前视频片段从中点拆成两个连续片段，各两秒；保留上一轮的画幅和字幕内容、样式、"
        "位置，保持原片来源和四秒总时长。"
    )
    graph = CamCatGraph(llm=QwenChatClient(get_settings()), retrieval=object())
    intent = graph.understand(
        CamCatState(query_text=instruction, current_document=archived["state"])
    )["intent"]
    (tmp_path / "intent.json").write_text(json.dumps(intent, ensure_ascii=False, indent=2))
    assert intent["caption_operation"] == "keep"
    result = graph.generate_commands(
        CamCatState(
            mode="edit",
            query_text=instruction,
            intent=intent,
            current_document=archived["state"],
            ranked_materials=[],
            source_materials=prepare_source_candidates(project.metadata["source_segments"]),
        )
    )
    (tmp_path / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    after = EditingProjectV2.model_validate(result["simulated_document"])
    original = [s for t in project.timeline.tracks if isinstance(t, VideoTrack) for s in t.segments]
    clips = [s for t in after.timeline.tracks if isinstance(t, VideoTrack) for s in t.segments]
    assert len(original) == 1 and len(clips) == 2
    clips.sort(key=lambda s: s.timeline_start_us)
    assert [s.timeline_duration_us for s in clips] == [2_000_000, 2_000_000]
    assert [s.timeline_start_us for s in clips] == [0, 2_000_000]
    assert [s.source_id for s in clips] == [original[0].source_id] * 2
    assert [s.source_start_us for s in clips] == [
        original[0].source_start_us,
        original[0].source_start_us + 2_000_000,
    ]
    assert [s.source_duration_us for s in clips] == [2_000_000, 2_000_000]
    assert after.canvas == project.canvas
    assert [t for t in after.timeline.tracks if isinstance(t, TextTrack)] == [
        t for t in project.timeline.tracks if isinstance(t, TextTrack)
    ]
