import json

import httpx
from camcat.config import Settings
from camcat.services.providers import QwenChatClient
from camcat.services.telemetry import capture_provider_calls


def settings(**updates):
    values = {
        "environment": "test",
        "embedding_base_url": "http://unit.invalid",
        "embedding_api_key": "unit",
        "reranker_base_url": "http://unit.invalid",
        "reranker_api_key": "unit",
        "llm_base_url": "http://unit.invalid",
        "llm_api_key": "unit",
        "asr_base_url": "http://unit.invalid",
        "asr_api_key": "unit",
    }
    return Settings(_env_file=None, **{**values, **updates})


def test_gateway_health_uses_authenticated_models_endpoint(monkeypatch):
    client = QwenChatClient(settings(llm_health_path="/v1/models"))
    seen = []
    monkeypatch.setattr(client, "request", lambda method, path: seen.append((method, path)))
    client.healthcheck()
    assert seen == [("GET", "/v1/models")]


def test_gateway_completion_has_explicit_output_budget(monkeypatch):
    client = QwenChatClient(settings(llm_max_output_tokens=4096))
    seen = []

    def request(method, path, **kwargs):
        seen.append(kwargs["json"])
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    monkeypatch.setattr(client, "request", request)
    assert client.json_completion(system="unit", user="unit") == {"ok": True}
    assert seen[0]["max_tokens"] == 4096


def test_default_completion_preserves_provider_output_default(monkeypatch):
    client = QwenChatClient(settings())
    seen = []

    def request(method, path, **kwargs):
        seen.append(kwargs["json"])
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    monkeypatch.setattr(client, "request", request)
    client.json_completion(system="unit", user="unit")
    assert "max_tokens" not in seen[0]


def test_provider_receipt_links_gateway_request_and_usage(tmp_path):
    client = QwenChatClient(settings(gateway_receipts_dir=str(tmp_path)))
    usage = {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}

    def respond(request):
        return httpx.Response(
            200,
            headers={"X-Request-ID": "gateway-ledger-request"},
            json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": usage},
        )

    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(respond))
    with capture_provider_calls() as calls:
        client.json_completion(system="unit", user="unit")
    assert calls[0]["gateway_request_id"] == "gateway-ledger-request"
    assert calls[0]["usage"] == usage
    saved = list(tmp_path.glob("*.json"))
    assert len(saved) == 1
    record = json.loads(saved[0].read_text())
    assert record["gateway_request_id"] == "gateway-ledger-request"
    assert record["usage"] == usage
    assert "messages" not in record
    assert "api_key" not in record
