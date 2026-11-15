"""Real Qwen extraction obeys the typed shape and explicit delivery preferences."""

import os

import pytest
from camcat.agent.graph import CamCatGraph, CamCatState
from camcat.config import get_settings
from camcat.services.providers import QwenChatClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("CAMCAT_LOCAL_MODELS_EXTERNAL") != "1",
        reason="the configured local Qwen provider is required",
    ),
]


@pytest.mark.parametrize(
    ("instruction", "policy"),
    [
        ("寻找与参考图相似的有活力镜头，剪成4秒竖屏短片。", "auto"),
        ("Use each picked clip in full. No extra captions, music or transitions.", "forbid"),
    ],
)
def test_real_requirement_extraction_preserves_explicit_policy(instruction, policy):
    graph = CamCatGraph(llm=QwenChatClient(get_settings()), retrieval=object())
    result = graph.understand(CamCatState(query_text=instruction))
    intent = result["intent"]
    assert isinstance(intent["search_query"], str)
    assert intent["planning_mode"] in {"commands", "source_sequence"}
    assert intent["operation_scope"] in {"refine", "structural", "unknown"}
    assert "default" not in intent and "url" not in intent
    assert intent["delivery_constraints"]["subtitles"] == policy
    assert intent["delivery_constraints"]["transitions"] == policy
    assert intent["delivery_constraints"]["full_source_clips"] == (policy == "forbid")
