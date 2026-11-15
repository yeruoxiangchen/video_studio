from __future__ import annotations

from uuid import uuid4

import pytest
from camcat.api import app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def project_with_sessions(client: TestClient, project_id: str) -> dict:
    params = {"include_sessions": True, "limit": 100}
    seen = set()
    while True:
        expanded = client.get("/api/v1/projects", params=params)
        assert expanded.status_code == 200
        page = expanded.json()
        item = next((row for row in page["items"] if row["project_id"] == project_id), None)
        if item is not None:
            return item
        cursor = page["next_cursor"]
        assert cursor and cursor not in seen, "Created project was missing from cursor traversal"
        seen.add(cursor)
        params["cursor"] = cursor


def test_native_render_policy_is_persisted_with_version_conflict_protection() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        project = client.post("/api/v1/projects", json={"name": f"render-policy-{uuid4()}"})
        assert project.status_code == 201
        packet = client.post(
            "/api/v1/editing/sessions",
            json={
                "project_id": project.json()["project_id"],
                "current_goal": "preserve original color and audio",
            },
        )
        assert packet.status_code == 201
        session = packet.json()["editing_session_id"]
        body = {
            "base_version": 1,
            "reason": "render policy contract",
            "commands": [
                {
                    "type": "set_render_policy",
                    "policy": {"preserve_color": True, "preserve_audio": True},
                }
            ],
        }
        changed = client.post(f"/api/v1/editing/sessions/{session}/commands", json=body)
        assert changed.status_code == 200, changed.text
        saved = client.get(f"/api/v1/editing/sessions/{session}").json()
        assert saved["state_version"] == 2
        assert saved["state"]["metadata"]["render_policy"] == {
            "preserve_color": True,
            "preserve_audio": True,
        }
        conflict = client.post(f"/api/v1/editing/sessions/{session}/commands", json=body)
        assert conflict.status_code == 409
        client.delete(f"/api/v1/editing/sessions/{session}")


def test_project_session_command_conflict_audit_and_soft_delete_journey() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        project = client.post("/api/v1/projects", json={"name": f"integration-{uuid4()}"})
        assert project.status_code == 201
        project_id = project.json()["project_id"]

        projects = client.get("/api/v1/projects", params={"limit": 1})
        assert projects.status_code == 200
        assert "items" in projects.json() and "next_cursor" in projects.json()

        created = client.post(
            "/api/v1/editing/sessions",
            json={"project_id": project_id, "current_goal": "integration edit"},
        )
        assert created.status_code == 201
        session_id = created.json()["editing_session_id"]

        updated = client.post(
            f"/api/v1/editing/sessions/{session_id}/commands",
            json={
                "base_version": 1,
                "commands": [{"type": "update_title", "title": "v2"}],
                "reason": "integration command",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["state_version"] == 2

        item = project_with_sessions(client, project_id)
        assert [
            (session["editing_session_id"], session["state_version"])
            for session in item["sessions"]
        ] == [(session_id, 2)]

        conflict = client.post(
            f"/api/v1/editing/sessions/{session_id}/commands",
            json={
                "base_version": 1,
                "commands": [{"type": "update_title", "title": "stale"}],
                "reason": "stale command",
            },
        )
        assert conflict.status_code == 409
        details = conflict.json()["error"]["details"]
        assert details["expected_version"] == 1
        assert details["current_version"] == 2
        assert details["current_patch"]["result_version"] == 2

        versions = client.get(f"/api/v1/editing/sessions/{session_id}/versions")
        patches = client.get(f"/api/v1/editing/sessions/{session_id}/patches")
        audit = client.get(f"/api/v1/editing/sessions/{session_id}/audit")
        assert [item["version"] for item in versions.json()["items"]] == [1, 2]
        assert len(patches.json()["items"]) == 1
        assert {item["event_type"] for item in audit.json()["items"]} >= {
            "editing_session_created",
            "state_patch_applied",
        }

        deleted = client.delete(f"/api/v1/editing/sessions/{session_id}")
        assert deleted.status_code == 204
        missing = client.get(f"/api/v1/editing/sessions/{session_id}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
        item = project_with_sessions(client, project_id)
        assert item["sessions"] == []
