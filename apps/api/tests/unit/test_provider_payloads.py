from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from camcat.config import Settings
from camcat.services.providers import (
    ProviderError,
    QwenChatClient,
    QwenEmbeddingClient,
    QwenRerankerClient,
    QwenVisualAnalysisClient,
    SegmentSemantics,
    SourceClipSemantics,
    parse_structured_content,
)
from pydantic import SecretStr, ValidationError


def settings() -> Settings:
    return Settings.model_construct(
        embedding_base_url="https://provider.test",
        embedding_api_key=SecretStr("secret"),
        embedding_model="Qwen/Qwen3-VL-Embedding-8B",
        embedding_dimension=2048,
        reranker_base_url="https://provider.test",
        reranker_api_key=SecretStr("secret"),
        reranker_model="Qwen/Qwen3-VL-Reranker-8B",
        llm_base_url="https://provider.test",
        llm_api_key=SecretStr("secret"),
        asr_base_url="https://provider.test",
        asr_api_key=SecretStr("secret"),
        provider_timeout_seconds=10,
        provider_max_retries=0,
        embedding_video_fps=1.0,
    )


def test_source_visual_contract_is_bounded_and_retains_shot_evidence(tmp_path: Path):
    client = QwenVisualAnalysisClient(settings())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unit input")
    captured = {}

    def request(method, path, **kwargs):
        captured.update(kwargs["data"])
        return {
            "analysis": {
                "description": "A person walks away",
                "scene": "gallery",
                "composition": "wide shot",
                "risk_score": 0,
                "subject_view": "back",
                "camera_motion": "push in",
                "temporal_evidence": [
                    "Beginning: person walking toward the camera",
                    "End: person stops; the camera keeps moving closer",
                ],
            },
            "usage": {"prompt_tokens": 120, "completion_tokens": 60},
        }

    client.request = request
    result = client.analyze_video(source, compact=True)
    import json

    schema = json.loads(captured["response_schema"])
    assert captured["max_tokens"] == "1536"
    assert schema["properties"]["description"]["maxLength"] <= 512
    assert result.subject_view == "back"
    assert result.camera_motion == "push in"
    assert {"opening_state", "closing_state"} <= set(schema["required"])
    assert result.temporal_evidence[-1].startswith("End:")
    assert client.last_analysis_usage == {"prompt_tokens": 120, "completion_tokens": 60}


def test_shot_size_cannot_be_misreported_as_camera_angle():
    with pytest.raises(ValidationError):
        SourceClipSemantics(
            description="A person's face",
            scene="indoor",
            composition="close view",
            risk_score=0,
            shot_size="close-up",
            camera_angle="Close-up",
        )


def test_structured_content_accepts_json_object() -> None:
    assert parse_structured_content('{"summary":"ok"}') == {"summary": "ok"}


def test_chat_requests_json_correction_once_before_failing():
    config = settings().model_copy(update={"llm_model": "configured-model"})
    client = QwenChatClient(config)
    requests = []

    def request(method, path, **kwargs):
        requests.append(kwargs["json"])
        content = '{"summary":' if len(requests) == 1 else '{"summary":"ok"}'
        return {"choices": [{"message": {"content": content}}]}

    client.request = request
    assert client.json_completion(system="Return JSON", user="目标") == {"summary": "ok"}
    assert len(requests) == 2
    assert "valid JSON" in requests[1]["messages"][0]["content"]


def test_caller_can_request_greedy_decoding_across_json_correction():
    client = QwenChatClient(settings())
    packets = []

    def request(method, path, **kwargs):
        packets.append(kwargs["json"])
        content = '{"summary":' if len(packets) == 1 else '{"summary":"ok"}'
        return {"choices": [{"message": {"content": content}}]}

    client.request = request
    assert client.json_completion(system="Return JSON", user="source", temperature=0) == {
        "summary": "ok"
    }
    assert [packet["temperature"] for packet in packets] == [0, 0]


def test_structured_content_normalizes_top_level_model_array() -> None:
    assert parse_structured_content('[{"text":"第一幕","start":0,"end":1}]') == {
        "items": [{"text": "第一幕", "start": 0, "end": 1}]
    }


def test_structured_content_removes_markdown_json_fence() -> None:
    assert parse_structured_content('```json\n{"commands":[]}\n```') == {"commands": []}


def test_structured_content_normalizes_only_escaped_whitespace_outside_strings():
    assert parse_structured_content(r'{\n"summary":"line\nnext",\n"path":"C:\\new"\n}') == {
        "summary": "line\nnext",
        "path": "C:\\new",
    }


def test_embedding_contract_uploads_original_video_as_multipart(tmp_path: Path) -> None:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"real-video-bytes")
    client = QwenEmbeddingClient(settings())
    captured: dict[str, Any] = {}

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(method=method, path=path, **kwargs)
        captured["video_bytes"] = kwargs["files"]["video"][1].read()
        return {"data": [{"embedding": [0.5] * 2048}]}

    client.request = request  # type: ignore[method-assign]
    vector = client.embed_video(video, text="sunset")

    assert vector == [0.5] * 2048
    assert captured["path"] == "/v1/embeddings"
    assert "json" not in captured
    assert captured["data"] == {
        "model": "Qwen/Qwen3-VL-Embedding-8B",
        "dimensions": "2048",
        "text": "sunset",
        "fps": "1.0",
    }
    filename, _stream, media_type = captured["files"]["video"]
    assert filename == "source.mp4"
    assert media_type == "video/mp4"
    assert captured["video_bytes"] == b"real-video-bytes"


def test_embedding_contract_rejects_multiple_vectors_instead_of_averaging() -> None:
    client = QwenEmbeddingClient(settings())
    with pytest.raises(ProviderError, match="exactly one embedding"):
        client._extract({"data": [{"embedding": [1.0] * 2048}, {"embedding": [2.0] * 2048}]})


def test_mixed_rerank_preserves_text_image_and_document_metadata() -> None:
    client = QwenRerankerClient(settings())
    captured: dict[str, Any] = {}

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(method=method, path=path, **kwargs)
        return {"results": [{"index": 0, "relevance_score": 0.9}]}

    client.request = request  # type: ignore[method-assign]
    query = {"text": "sunset", "image_base64": "data:image/jpeg;base64,YQ=="}
    documents = [{"text": "beach", "metadata": {"license": "CC0", "tags": ["sea"]}}]

    assert client.rerank(query, documents) == [0.9]
    assert captured["path"] == "/v1/rerank"
    assert captured["json"]["query"] == query
    assert captured["json"]["documents"] == documents


def test_healthcheck_uses_provider_health_contract() -> None:
    client = QwenEmbeddingClient(settings())
    calls: list[tuple[str, str]] = []
    client.request = lambda method, path, **_kwargs: calls.append((method, path)) or {}  # type: ignore[method-assign]

    client.healthcheck()

    assert calls == [("GET", "/health")]


def test_segment_semantics_rejects_unbounded_risk_and_empty_description() -> None:
    with pytest.raises(ValidationError):
        SegmentSemantics.model_validate(
            {
                "description": "",
                "scene": "shore",
                "actions": [],
                "people": [],
                "composition": "wide",
                "tags": [],
                "event_type": "travel",
                "risk_score": 2,
                "risk_labels": [],
            }
        )


@pytest.mark.parametrize("enabled", [False, True])
def test_command_schema_is_sent_only_to_explicitly_enabled_providers(enabled: bool) -> None:
    client = QwenChatClient(settings().model_copy(update={"llm_json_schema_enabled": enabled}))
    captured: dict[str, Any] = {}

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": '{"commands": []}'}}]}

    client.request = request  # type: ignore[method-assign]
    schema = {"type": "object", "properties": {"commands": {"type": "array"}}}
    client.json_completion(system="Return commands", user="Edit", response_schema=schema)
    response_format = captured["json"]["response_format"]
    assert response_format["type"] == ("json_schema" if enabled else "json_object")
    if enabled:
        assert response_format["json_schema"]["schema"] == schema
