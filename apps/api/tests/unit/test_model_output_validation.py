"""Pure schema and provider-policy regressions; no external inference is simulated."""

import json
from pathlib import Path

import httpx
import pytest
from camcat.config import Settings
from camcat.services.providers import ProviderError, QwenVisualAnalysisClient
from camcat.worker import FailureCategory, _failure_category


def client_with_transport(handler):
    settings = Settings(
        llm_base_url="http://unit.invalid",
        llm_api_key="unit-only",
        provider_max_retries=2,
    )
    client = QwenVisualAnalysisClient(settings)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_schema_failure_is_explicit_and_not_an_infrastructure_retry():
    from services.local_models.validation import ModelOutputError, validate_model_output

    schema = {
        "type": "object",
        "properties": {"subject_motion": {"enum": ["approaching", "uncertain"]}},
    }
    with pytest.raises(ModelOutputError) as caught:
        validate_model_output({"subject_motion": "walking"}, schema)
    details = caught.value.details()
    assert details["code"] == "model_output_invalid"
    assert details["retryable"] is False
    assert details["violations"] == [{"path": ["subject_motion"], "validator": "enum"}]
    assert "walking" not in json.dumps(details)


@pytest.mark.parametrize("status", [401, 403, 422])
def test_deterministic_provider_errors_do_not_repeat_the_worker_job(status):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(status, json={"detail": "configuration or input error"})

    client = client_with_transport(handler)
    with pytest.raises(ProviderError) as caught:
        client.request("POST", "/v1/analyze")
    assert len(attempts) == 1
    assert _failure_category(caught.value) == FailureCategory.DETERMINISTIC_VALIDATION


def test_visual_schema_failure_has_one_corrective_call_and_keeps_the_generated_facts(
    tmp_path: Path,
):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                422,
                json={
                    "detail": {
                        "code": "model_output_invalid",
                        "retryable": False,
                        "violations": [{"path": ["subject_motion"], "validator": "enum"}],
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "analysis": {
                    "description": "A person walks across the frame",
                    "scene": "gallery",
                    "composition": "wide shot",
                    "risk_score": 0,
                    "actions": ["walking"],
                    "subject_motion": "moving across",
                    "temporal_evidence": ["Person begins walking", "Person exits the frame"],
                }
            },
        )

    client = client_with_transport(handler)
    source = tmp_path / "unit.mp4"
    source.write_bytes(b"unit transport input")
    result = client.analyze_video(source, compact=True)
    assert len(requests) == 2
    assert result.subject_motion == "moving across"
    assert result.actions == ["walking"]
    assert b"subject_motion" in requests[1].content


def test_persistently_invalid_model_output_stops_after_the_corrective_call(tmp_path: Path):
    from camcat.services.providers import ProviderOutputError

    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(
            422,
            json={
                "detail": {
                    "code": "model_output_invalid",
                    "retryable": False,
                    "violations": [{"path": ["subject_motion"], "validator": "enum"}],
                }
            },
        )

    client = client_with_transport(handler)
    source = tmp_path / "unit.mp4"
    source.write_bytes(b"unit transport input")
    with pytest.raises(ProviderOutputError) as caught:
        client.analyze_video(source, compact=True)
    assert len(attempts) == 2
    assert _failure_category(caught.value) == FailureCategory.DETERMINISTIC_VALIDATION


def test_context_budget_error_keeps_machine_readable_counts_without_a_correction_loop():
    detail = {"code": "context_budget_exceeded", "prompt_tokens": 15000, "max_input_tokens": 12288}
    client = client_with_transport(lambda request: httpx.Response(422, json={"detail": detail}))
    with pytest.raises(ProviderError) as caught:
        client.request("POST", "/v1/chat/completions")
    assert caught.value.details == detail
    assert caught.value.error_code == "context_budget_exceeded"
