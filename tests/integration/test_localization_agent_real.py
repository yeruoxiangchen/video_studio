"""Actual model routing, persistent jobs, CAS continuation and unapproved delivery."""

from __future__ import annotations

import os
import time
from uuid import uuid4

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.external]


def terminal(client: httpx.Client, job_id: str) -> dict:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        response = client.get("/api/v1/jobs/" + job_id)
        response.raise_for_status()
        job = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled", "dead_letter"}:
            return job
        time.sleep(0.5)
    pytest.fail("Actual localization task exceeded three minutes")


@pytest.mark.skipif(not os.environ.get("CAMCAT_BUSINESS_SOURCE_JOB"), reason="Real source required")
def test_chat_translation_dubbing_and_cas_conflict():
    with httpx.Client(base_url="http://127.0.0.1:18960", timeout=120, trust_env=False) as client:

        def create() -> str:
            response = client.post(
                "/api/v1/editing/sessions",
                json={
                    "source_job_id": os.environ["CAMCAT_BUSINESS_SOURCE_JOB"],
                    "current_goal": f"Agent 译制测试 {uuid4()}",
                },
            )
            response.raise_for_status()
            return "/api/v1/editing/sessions/" + response.json()["editing_session_id"]

        path = create()
        other = create()
        try:
            editing = client.post(
                path + "/agent/dispatch", json={"base_version": 1, "instruction": "截取前四秒"}
            )
            assert editing.status_code == 200, editing.text
            assert editing.json()["action"] == "edit"
            assert editing.json()["session"]["state_version"] == 1
            unsupported = client.post(
                path + "/agent/dispatch",
                json={"base_version": 1, "instruction": "翻译成法文并配音"},
            )
            assert unsupported.status_code == 422, unsupported.text
            assert client.get(path).json()["state_version"] == 1

            question = client.post(
                path + "/agent/dispatch",
                json={
                    "base_version": 1,
                    "instruction": "翻译和配音有什么区别？先解释一下，别执行。",
                },
            )
            question.raise_for_status()
            assert question.json()["action"] == "clarify", question.text
            assert question.json().get("job") is None

            # A user combines the two recognized fragments into one spoken phrase window.
            # This is input editing, never a replacement for actual model output.
            prepared = client.post(path + "/localization/prepare", json={"base_version": 1})
            prepared.raise_for_status()
            ctx = client.get(path + "/localization").json()
            document = prepared.json()["state"]
            duration = round(
                float(document["metadata"]["source_media"][0]["duration_seconds"]) * 1e6
            )
            merged = client.post(
                path + "/commands",
                json={
                    "base_version": 2,
                    "reason": "User groups recognized fragments into a single narration window",
                    "commands": [
                        {"type": "remove_track", "track_id": ctx["source_track_id"]},
                        {
                            "type": "add_track",
                            "track": {
                                "type": "text",
                                "track_id": "spoken-phrase",
                                "name": "原文口播",
                                "segments": [
                                    {
                                        "type": "text",
                                        "segment_id": "spoken-phrase-1",
                                        "start_us": 0,
                                        "duration_us": duration,
                                        "text": "".join(c["text"] for c in ctx["source_cues"]),
                                    }
                                ],
                            },
                        },
                    ],
                },
            )
            merged.raise_for_status()
            submitted = client.post(
                path + "/agent/dispatch",
                json={
                    "base_version": 3,
                    "instruction": "把原文字幕翻译成英文并配音，允许一次时长改写",
                },
            )
            submitted.raise_for_status()
            assert submitted.json()["action"] == "translate_and_dub"
            first = terminal(client, submitted.json()["job"]["job_id"])
            assert first["status"] == "succeeded", first
            second = terminal(client, first["result"]["next_job_id"])
            assert second["status"] == "succeeded", second
            assert first["result"]["applied_version"] == 4
            assert second["result"]["applied_version"] == 5
            context = client.get(path + "/localization").json()
            assert context["delivery"]["state"] == "needs_review"
            assert context["delivery"]["reviewed_version"] is None
            assert len(context["source_cues"]) == len(second["result"]["audio"])
            duplicate = client.post(
                path + "/localization/" + first["job_id"] + "/apply", json={"base_version": 5}
            )
            duplicate.raise_for_status()
            assert duplicate.json()["state_version"] == 5

            # A human edit arriving while the model translates must win the CAS.
            pending = client.post(
                other + "/agent/dispatch",
                json={"base_version": 1, "instruction": "翻译成英文并配音，允许一次时长改写"},
            )
            pending.raise_for_status()
            patch = client.post(
                other + "/commands",
                json={
                    "base_version": pending.json()["session"]["state_version"],
                    "reason": "Real concurrent human edit",
                    "commands": [{"type": "update_title", "title": "人工新版本"}],
                },
            )
            patch.raise_for_status()
            failed = terminal(client, pending.json()["job"]["job_id"])
            assert failed["status"] == "failed", failed
            assert not (failed.get("result") or {}).get("next_job_id")
            assert client.get(other).json()["state"]["title"] == "人工新版本"
        finally:
            client.delete(path)
            client.delete(other)
