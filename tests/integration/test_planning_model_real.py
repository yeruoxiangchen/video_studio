"""Actual native planning endpoint; no provider responses are simulated."""

import json
import os
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

pytestmark = [pytest.mark.integration, pytest.mark.external]


@pytest.fixture
def native_planner():
    base = os.environ.get("CAMCAT_NATIVE_PLANNING_BASE_URL")
    if not base:
        pytest.skip("Set CAMCAT_NATIVE_PLANNING_BASE_URL to the real native planning service")
    root = Path(os.environ.get("CAMCAT_STUDIO_DATA", "/data/zjr/camcat_studio"))
    key = dotenv_values(root / "config/local-models.env")["CAMCAT_LOCAL_MODELS_API_KEY"]
    with httpx.Client(
        base_url=base, trust_env=False, timeout=180, headers={"Authorization": "Bearer " + str(key)}
    ) as client:
        yield client


def test_the_loaded_native_model_matches_its_configured_identity(native_planner):
    response = native_planner.get("/health", timeout=3)
    response.raise_for_status()
    assert response.json()["status"] == "ok"
    actual = native_planner.get("/v1/models").json()
    assert any(model["id"] == "Qwen/Qwen3.6-27B" for model in actual["data"])


def test_native_json_output_uses_the_requested_schema_and_preserves_unicode(native_planner):
    response = native_planner.post(
        "/v1/chat/completions",
        json={
            "model": "Qwen/Qwen3.6-27B",
            "messages": [{"role": "user", "content": "用中文写一句简短的视频剪辑描述。"}],
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": 0,
            "max_tokens": 128,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "description",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "description": {"type": "string", "minLength": 1, "maxLength": 32}
                        },
                        "required": ["description"],
                    },
                },
            },
        },
    )
    response.raise_for_status()
    result = response.json()
    content = json.loads(result["choices"][0]["message"]["content"])
    assert set(content) == {"description"} and 1 <= len(content["description"]) <= 32
    assert any("\u3400" <= char <= "\u9fff" for char in content["description"])


def test_application_client_rejects_a_real_truncated_native_completion(native_planner):
    from camcat.config import get_settings
    from camcat.services.narrative import NativeNarrativeClient
    from camcat.services.providers import ProviderOutputError
    from pydantic import SecretStr

    settings = get_settings().model_copy(
        update={
            "narrative_base_url": str(native_planner.base_url),
            "narrative_model": "Qwen/Qwen3.6-27B",
            "narrative_api_key": SecretStr(native_planner.headers["Authorization"][7:]),
        }
    )
    client = NativeNarrativeClient(settings)
    try:
        with pytest.raises(ProviderOutputError, match="token budget"):
            client.complete(
                system="Return the requested JSON.",
                user="Write one short sentence describing a video.",
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"description": {"type": "string", "minLength": 20}},
                    "required": ["description"],
                },
                max_tokens=1,
                thinking=False,
            )
    finally:
        client.client.close()
