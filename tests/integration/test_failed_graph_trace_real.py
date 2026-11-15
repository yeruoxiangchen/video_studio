"""Real API/provider budget rejection preserves the saved version and failed-node evidence."""

import os
from uuid import uuid4

import httpx
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("CAMCAT_LOCAL_MODELS_EXTERNAL") != "1",
        reason="the real local API and Qwen provider are required",
    ),
]


def test_real_context_rejection_keeps_the_version_and_persists_the_failed_node():
    base = os.environ.get("CAMCAT_TEST_API_URL", "http://127.0.0.1:18960")
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        project = client.post("/api/v1/projects", json={"name": "Context guard " + uuid4().hex[:8]})
        project.raise_for_status()
        response = client.post(
            "/api/v1/editing/sessions",
            json={
                "project_id": project.json()["project_id"],
                "current_goal": "Provider budget contract",
            },
        )
        response.raise_for_status()
        before = response.json()
        session_id = before["editing_session_id"]
        rejected = client.post(
            f"/api/v1/editing/sessions/{session_id}/agent",
            json={
                "base_version": before["state_version"],
                # This unassigned Unicode scalar uses two pieces per character in
                # the deployed tokenizer and fits the API's 8000-character limit.
                "instruction": "\u0378" * 7500,
            },
        )
        assert rejected.status_code == 422, rejected.text[:400]
        error = rejected.json()["error"]
        assert error["code"] == "context_budget_exceeded"
        assert error["details"]["prompt_tokens"] > error["details"]["max_input_tokens"]
        current = client.get(f"/api/v1/editing/sessions/{session_id}")
        current.raise_for_status()
        assert current.json()["state_version"] == before["state_version"]
        assert current.json()["state"] == before["state"]
        runs = client.get("/api/v1/graph-runs", params={"limit": 100})
        runs.raise_for_status()
        failed = next(
            row for row in runs.json()["items"] if row["editing_session_id"] == session_id
        )
        assert failed["status"] == "failed"
        trace = failed["node_trace"][-1]
        assert trace["node_name"] == "understand_requirement"
        assert trace["status"] == "failed"
        assert len(trace["provider_calls"]) == 1
        assert trace["provider_calls"][0]["error_code"] == "context_budget_exceeded"
        assert trace["provider_calls"][0]["status_code"] == 422


def test_real_edit_without_original_reports_an_actionable_error():
    base = os.environ.get("CAMCAT_TEST_API_URL", "http://127.0.0.1:18960")
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "Missing original " + uuid4().hex[:8]}
        )
        project.raise_for_status()
        created = client.post(
            "/api/v1/editing/sessions",
            json={"project_id": project.json()["project_id"], "current_goal": "Edit without input"},
        )
        created.raise_for_status()
        before = created.json()
        session = before["editing_session_id"]
        rejected = client.post(
            f"/api/v1/editing/sessions/{session}/agent",
            json={"base_version": before["state_version"], "instruction": "剪成四秒短片。"},
        )
        assert rejected.status_code == 422, rejected.text[:400]
        assert rejected.json()["error"]["code"] == "source_required"
        assert "上传" in rejected.json()["error"]["message"]
        current = client.get(f"/api/v1/editing/sessions/{session}")
        current.raise_for_status()
        assert current.json()["state_version"] == before["state_version"]
        assert current.json()["state"] == before["state"]
