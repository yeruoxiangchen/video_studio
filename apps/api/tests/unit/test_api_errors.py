from __future__ import annotations

import asyncio
import json

from camcat.api import internal_error_handler
from starlette.requests import Request


def test_missing_original_is_an_actionable_validation_error() -> None:
    from camcat.agent.errors import AgentInputError
    from camcat.api import agent_input_error_handler

    request = Request({"type": "http", "method": "POST", "path": "/agent", "headers": []})
    request.state.request_id = "source-required"
    response = asyncio.run(
        agent_input_error_handler(request, AgentInputError("请上传原片", code="source_required"))
    )
    assert response.status_code == 422
    assert json.loads(response.body)["error"] == {
        "code": "source_required",
        "message": "请上传原片",
        "details": {},
        "request_id": "source-required",
    }


def test_rejected_edit_plan_keeps_the_reason_in_a_structured_error() -> None:
    from camcat.agent.errors import EditPlanRejected
    from camcat.api import agent_input_error_handler

    request = Request({"type": "http", "method": "POST", "path": "/agent", "headers": []})
    request.state.request_id = "plan-rejected"
    error = EditPlanRejected("split point must be inside the source range")
    response = asyncio.run(agent_input_error_handler(request, error))
    assert response.status_code == 422
    packet = json.loads(response.body)["error"]
    assert packet["code"] == "edit_plan_invalid"
    assert packet["details"]["validation_error"] == "split point must be inside the source range"


def test_unhandled_error_uses_envelope_without_exception_details() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/boom",
            "headers": [(b"x-request-id", b"request-123")],
        }
    )
    request.state.request_id = "request-123"

    response = asyncio.run(internal_error_handler(request, RuntimeError("secret filesystem path")))
    payload = json.loads(response.body)

    assert response.status_code == 500
    assert payload == {
        "error": {
            "code": "internal_error",
            "message": "服务器无法完成请求。",
            "details": {},
            "request_id": "request-123",
        }
    }
    assert "secret filesystem path" not in response.body.decode()
