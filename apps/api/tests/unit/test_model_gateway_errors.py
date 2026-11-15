"""Unit boundary tests with an injected runtime, independent of external model verification."""

import json

import pytest
from fastapi.testclient import TestClient

from services.local_models.server import VISUAL_MODEL, app, authorize, engine


@pytest.mark.parametrize("endpoint", ["chat", "analyze"])
def test_generated_schema_violation_returns_a_bounded_422_without_generated_content(
    tmp_path, endpoint
):
    class UnitRuntime:
        root = tmp_path
        visual_precision = "nf4"

        def generate(self, *args, **kwargs):
            return {"parsed": {"subject_motion": "walking"}, "timings": {"generation_seconds": 0.1}}

    (tmp_path / "tmp").mkdir()
    schema = {
        "type": "object",
        "properties": {"subject_motion": {"enum": ["approaching", "uncertain"]}},
        "required": ["subject_motion"],
        "additionalProperties": False,
    }
    app.dependency_overrides[engine] = lambda: UnitRuntime()
    app.dependency_overrides[authorize] = lambda: None
    client = TestClient(app)
    try:
        if endpoint == "chat":
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": VISUAL_MODEL,
                    "messages": [{"role": "user", "content": "unit"}],
                    "response_format": {"type": "json_schema", "json_schema": {"schema": schema}},
                },
            )
        else:
            response = client.post(
                "/v1/analyze",
                data={
                    "model": VISUAL_MODEL,
                    "prompt": "unit",
                    "response_schema": json.dumps(schema),
                },
                files={"video": ("unit.mp4", b"unit transport bytes", "video/mp4")},
            )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "model_output_invalid"
        assert detail["retryable"] is False
        assert detail["metrics"]["generation_seconds"] == 0.1
        assert detail["violations"] == [{"path": ["subject_motion"], "validator": "enum"}]
        assert "walking" not in response.text
    finally:
        client.close()
        app.dependency_overrides.clear()
