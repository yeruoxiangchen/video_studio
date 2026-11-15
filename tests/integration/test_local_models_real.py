from __future__ import annotations

import os

import httpx
import pytest
from camcat.config import get_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.skipif(
        os.environ.get("CAMCAT_LOCAL_MODELS_EXTERNAL") != "1",
        reason="requires the real pinned local Qwen model service",
    ),
]


def test_invalid_visual_input_is_rejected_instead_of_embedding_null_text() -> None:
    settings = get_settings()
    with httpx.Client(
        base_url=settings.embedding_base_url,
        timeout=settings.provider_timeout_seconds,
        trust_env=False,
        headers={"Authorization": f"Bearer {settings.embedding_api_key.get_secret_value()}"},
    ) as client:
        response = client.post(
            "/v1/embeddings",
            data={"model": settings.embedding_model, "dimensions": "2048"},
            files={"image": ("invalid.jpg", b"invalid-image-input", "image/jpeg")},
        )
        assert response.status_code == 422
        assert "embedding" not in response.json()
        assert "preprocessing failed" in response.json()["detail"]


def test_expired_generation_releases_the_model_for_the_next_real_request() -> None:
    settings = get_settings()
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 128,
                "maxItems": 128,
                "items": {"type": "integer"},
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    with httpx.Client(
        base_url=settings.llm_base_url,
        timeout=30,
        trust_env=False,
        headers={"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"},
    ) as client:
        expired = client.post(
            "/v1/chat/completions",
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": "Generate 128 integers in items."}],
                "response_format": {"type": "json_schema", "json_schema": {"schema": schema}},
                "generation_timeout_seconds": 1,
                "max_tokens": 1024,
            },
        )
        assert expired.status_code == 408
        following = client.post(
            "/v1/chat/completions",
            json={
                "model": settings.llm_model,
                "generation_timeout_seconds": 25,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Return JSON with a short English description of morning light "
                            "in description."
                        ),
                    }
                ],
                "max_tokens": 80,
            },
        )
        following.raise_for_status()
        import json

        content = json.loads(following.json()["choices"][0]["message"]["content"])
        assert str(content.get("description", "")).strip()
