"""Unit-only transport metadata capture; no external provider result is claimed."""

import json

import httpx
import pytest
from camcat.agent.graph import CamCatGraph, CamCatState
from camcat.config import Settings
from camcat.services.providers import ProviderOutputError, QwenChatClient


def test_generation_phase_durations_are_retained_without_private_fields():
    from camcat.services.telemetry import safe_timings

    assert safe_timings({"first_token_seconds": 3.5, "decode_seconds": 1.2, "private": "x"}) == {
        "first_token_seconds": 3.5,
        "decode_seconds": 1.2,
    }


def test_failed_node_keeps_the_original_error_type_and_provider_call_receipt():
    from camcat.agent.trace import failure_node_trace

    client = QwenChatClient(Settings(llm_base_url="http://unit.invalid", llm_api_key="unit"))
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": '{"private":"do-not-record"}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 5,
                        "private": "do-not-record",
                    },
                    "camcat_metrics": {"generation_seconds": 0.1, "private": "do-not-record"},
                },
            )
        )
    )

    def failing_node(state):
        client.json_completion(system="unit", user="do-not-record")
        raise ProviderOutputError("unit model contract failure")

    with pytest.raises(ProviderOutputError) as caught:
        CamCatGraph._traced("generate_edit_plan", failing_node)(
            CamCatState(
                node_trace=[
                    {"node_name": "understand_requirement", "status": "completed", "duration_ms": 1}
                ]
            )
        )
    trace = failure_node_trace(caught.value)
    assert len(trace) == 2
    assert trace[-1]["status"] == "failed"
    assert trace[-1]["node_name"] == "generate_edit_plan"
    call = trace[-1]["provider_calls"][0]
    assert call["usage"] == {"prompt_tokens": 12, "completion_tokens": 5}
    assert call["finish_reason"] == "stop"
    assert call["timings"]["generation_seconds"] == 0.1
    assert call["latency_ms"] >= 0
    assert call["request_id"]
    assert "do-not-record" not in json.dumps(trace)


def test_successful_node_keeps_each_transient_attempt_separately():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"detail": "unit unavailable"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    client = QwenChatClient(Settings(llm_base_url="http://unit.invalid", llm_api_key="unit"))
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))

    def node(state):
        return {"intent": client.json_completion(system="unit", user="unit")}

    result = CamCatGraph._traced("understand_requirement", node)(CamCatState(node_trace=[]))
    receipts = result["node_trace"][0]["provider_calls"]
    assert [entry["status_code"] for entry in receipts] == [503, 200]
    assert receipts[0]["request_id"] != receipts[1]["request_id"]
