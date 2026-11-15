"""A real-model regression using the archived state of the failed browser revision."""

import json
import os
from pathlib import Path

import pytest
from camcat.agent.graph import CamCatGraph, CamCatState
from camcat.config import get_settings
from camcat.domain.project import AudioTrack, EditingProjectV2, TextTrack, VideoTrack
from camcat.editing.policies import prepare_source_candidates
from camcat.services.providers import QwenChatClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("CAMCAT_LOCAL_MODELS_EXTERNAL") != "1"
        or not os.getenv("CAMCAT_REVISION_REGRESSION_STATE"),
        reason="requires the real local provider and the archived browser state",
    ),
]


@pytest.mark.parametrize(
    ("instruction", "operation", "language"),
    [
        ("节奏更紧凑，字幕更短", "shorten", None),
        ("把现有英文字幕翻译成中文，其他设置不变", "translate", "zh"),
    ],
)
def test_real_requirement_distinguishes_caption_rewriting_from_translation(
    instruction: str, operation: str, language: str | None
):
    graph = CamCatGraph(llm=QwenChatClient(get_settings()), retrieval=object())
    intent = graph.understand(CamCatState(query_text=instruction))["intent"]
    assert intent["caption_operation"] == operation
    assert intent["caption_language"] == language


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        ("寻找与参考图相似的有活力镜头，剪成 4 秒竖屏短片", False),
        ("把视频画面向右平移 0.1，放大到 1.2 倍", True),
    ],
)
def test_real_requirement_distinguishes_output_ratio_from_video_placement(
    instruction: str, expected: bool
):
    graph = CamCatGraph(llm=QwenChatClient(get_settings()), retrieval=object())
    intent = graph.understand(CamCatState(query_text=instruction))["intent"]
    assert intent["video_transform_requested"] is expected


def test_real_caption_shortening_changes_words_and_preserves_video(tmp_path: Path):
    archived = json.loads(Path(os.environ["CAMCAT_REVISION_REGRESSION_STATE"]).read_text())
    project = EditingProjectV2.model_validate(archived["state"])
    graph = CamCatGraph(llm=QwenChatClient(get_settings()), retrieval=object())
    state = CamCatState(
        mode="edit",
        query_text=(
            "Shorten the existing caption text, keeping its language and meaning. "
            "Keep all video clips, their source ranges, transforms and the canvas unchanged."
        ),
        intent={
            "operation_scope": "refine",
            "external_material_ratio_limit": 0,
            "caption_operation": "shorten",
        },
        current_document=project.model_dump(mode="json", by_alias=True),
        ranked_materials=[],
        source_materials=prepare_source_candidates(project.metadata["source_segments"]),
    )
    result = graph.generate_commands(state)
    (tmp_path / "actual-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    after = EditingProjectV2.model_validate(result["simulated_document"])
    before_captions = {
        segment.segment_id: segment.text
        for track in project.timeline.tracks
        if isinstance(track, TextTrack)
        for segment in track.segments
    }
    after_captions = {
        segment.segment_id: segment.text
        for track in after.timeline.tracks
        if isinstance(track, TextTrack)
        for segment in track.segments
    }
    assert before_captions.keys() == after_captions.keys()
    assert any(
        sum(character.isalnum() for character in after_captions[key])
        < sum(character.isalnum() for character in text)
        for key, text in before_captions.items()
    ), "Removing punctuation alone does not shorten the caption wording"
    if all(text.isascii() for text in before_captions.values()):
        assert all(
            not any("\u3400" <= character <= "\u9fff" for character in text)
            for text in after_captions.values()
        ), "Shortening an English caption must preserve its language"
    assert all("camcat" in text.lower() for text in after_captions.values()), (
        "Keep the original proper name while shortening the surrounding wording"
    )
    assert after.canvas == project.canvas
    assert [t for t in after.timeline.tracks if not isinstance(t, TextTrack)] == [
        t for t in project.timeline.tracks if not isinstance(t, TextTrack)
    ]


def test_real_tighter_pacing_keeps_music_inside_the_revised_video(tmp_path: Path):
    archived = json.loads(Path(os.environ["CAMCAT_REVISION_REGRESSION_STATE"]).read_text())
    project = EditingProjectV2.model_validate(archived["state"])
    graph = CamCatGraph(llm=QwenChatClient(get_settings()), retrieval=object())
    instruction = "节奏更紧凑，字幕更短"
    intent = graph.understand(CamCatState(query_text=instruction))["intent"]
    result = graph.generate_commands(
        CamCatState(
            mode="edit",
            query_text=instruction,
            intent=intent,
            current_document=project.model_dump(mode="json", by_alias=True),
            ranked_materials=[],
            source_materials=prepare_source_candidates(project.metadata["source_segments"]),
        )
    )
    (tmp_path / "actual-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    after = EditingProjectV2.model_validate(result["simulated_document"])
    video_end = max(
        s.timeline_start_us + s.timeline_duration_us
        for t in after.timeline.tracks
        if isinstance(t, VideoTrack)
        for s in t.segments
    )
    assert all(
        s.timeline_start_us + s.timeline_duration_us <= video_end
        for t in after.timeline.tracks
        if isinstance(t, AudioTrack)
        for s in t.segments
    )
    original_words = {
        s.segment_id: s.text
        for t in project.timeline.tracks
        if isinstance(t, TextTrack)
        for s in t.segments
    }
    revised = [s for t in after.timeline.tracks if isinstance(t, TextTrack) for s in t.segments]
    assert any(
        sum(c.isalnum() for c in s.text) < sum(c.isalnum() for c in original_words[s.segment_id])
        for s in revised
    )
    assert all("camcat" in s.text.lower() for s in revised)
    assert after.canvas == project.canvas
