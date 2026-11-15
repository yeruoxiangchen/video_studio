import os

import httpx
import pytest
from camcat.config import get_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("CAMCAT_LOCAL_MODELS_EXTERNAL") != "1",
        reason="the real local Qwen provider is required",
    ),
]


def test_real_model_context_budget_returns_nonretryable_token_counts():
    settings = get_settings()
    with httpx.Client(timeout=120, trust_env=False) as client:
        response = client.post(
            settings.llm_base_url.rstrip("/") + "/v1/chat/completions",
            headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": " x" * 13000}],
                "response_format": {"type": "json_object"},
            },
        )
    assert response.status_code == 422, response.text[:300]
    detail = response.json()["detail"]
    assert detail["code"] == "context_budget_exceeded"
    assert detail["prompt_tokens"] > detail["max_input_tokens"] == 12288
