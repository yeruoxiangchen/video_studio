"""Real Postgres/queue/model/S3 tests. No replaced provider or injected job result."""

from __future__ import annotations

import os
import time
from uuid import uuid4

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.external]


def wait_job(client: httpx.Client, job_id: str) -> dict:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] == "succeeded":
            return job
        assert job["status"] not in {"failed", "cancelled", "dead_letter"}, job.get("error")
        time.sleep(1)
    pytest.fail("Real localization worker did not complete within 300 seconds")


@pytest.mark.skipif(
    os.environ.get("CAMCAT_LOCALIZATION_EXTERNAL") != "1",
    reason="set CAMCAT_LOCALIZATION_EXTERNAL=1 with the real API, worker and model services",
)
def test_real_translation_dubbing_versioning_cache_and_stale_audio() -> None:
    with httpx.Client(
        base_url=os.environ.get("CAMCAT_TEST_API_URL", "http://127.0.0.1:18960"),
        timeout=90,
        trust_env=False,
    ) as client:
        created = client.post("/api/v1/projects", json={"name": f"译制验收-{uuid4()}"})
        created.raise_for_status()
        session = client.post(
            "/api/v1/editing/sessions",
            json={
                "project_id": created.json()["project_id"],
                "current_goal": "真实译制链路验收",
            },
        )
        session.raise_for_status()
        session_id = session.json()["editing_session_id"]
        path = f"/api/v1/editing/sessions/{session_id}"
        try:
            text = client.post(
                path + "/commands",
                json={
                    "base_version": 1,
                    "reason": "Human-authored input subtitles; not model output",
                    "commands": [
                        {
                            "type": "add_track",
                            "track": {
                                "type": "text",
                                "track_id": "captions",
                                "name": "原文字幕",
                                "segments": [
                                    {
                                        "type": "text",
                                        "segment_id": "c1",
                                        "start_us": 0,
                                        "duration_us": 8_000_000,
                                        "text": "欢迎使用 CamCat。",
                                    },
                                    {
                                        "type": "text",
                                        "segment_id": "c2",
                                        "start_us": 8_000_000,
                                        "duration_us": 8_000_000,
                                        "text": "用 3 个步骤分享你的故事。",
                                    },
                                ],
                            },
                        }
                    ],
                },
            )
            text.raise_for_status()
            body = {"base_version": 2, "target_language": "en", "glossary": {"CamCat": "CamCat"}}
            first = client.post(path + "/localization/translate", json=body)
            first.raise_for_status()
            job_id = first.json()["job_id"]
            duplicate = client.post(path + "/localization/translate", json=body)
            assert duplicate.json()["job_id"] == job_id
            translated = wait_job(client, job_id)
            assert client.get(path).json()["state_version"] == 2
            assert {cue["cue_id"] for cue in translated["result"]["cues"]} == {"c1", "c2"}
            assert "CamCat" in translated["result"]["cues"][0]["text"]
            assert translated["result"]["subtitle_download_url"]
            applied = client.post(path + f"/localization/{job_id}/apply", json={"base_version": 2})
            applied.raise_for_status()
            assert applied.json()["state_version"] == 3
            again = client.post(path + f"/localization/{job_id}/apply", json={"base_version": 2})
            assert again.json()["state_version"] == 3
            assert client.post(path + "/localization/translate", json=body).status_code == 409

            dub = client.post(path + "/localization/dub", json={"base_version": 3})
            dub.raise_for_status()
            dubbed = wait_job(client, dub.json()["job_id"])
            assert len(dubbed["result"]["audio"]) == 2
            assert all(len(audio["sha256"]) == 64 for audio in dubbed["result"]["audio"])
            applied = client.post(
                path + f"/localization/{dubbed['job_id']}/apply", json={"base_version": 3}
            )
            applied.raise_for_status()
            assert applied.json()["state_version"] == 4
            context = client.get(path + "/localization").json()
            assert not context["audio_outdated"]
            assert context["target_track_id"] == "localization-subtitles"
            assert {source["origin"] for source in applied.json()["state"]["sources"]} >= {
                "generated_audio",
            }

            second = client.post(path + "/localization/dub", json={"base_version": 4})
            second.raise_for_status()
            cached = wait_job(client, second.json()["job_id"])
            assert cached["result"]["cache_hits"] == 2
            assert cached["result"]["tts_api_calls"] == 0
            cached_apply = client.post(
                path + f"/localization/{cached['job_id']}/apply", json={"base_version": 4}
            )
            cached_apply.raise_for_status()
            changed = client.post(
                path + "/commands",
                json={
                    "base_version": 5,
                    "reason": "Verify stale speech is rejected",
                    "commands": [
                        {
                            "type": "update_text_segment",
                            "segment_id": context["cues"][0]["cue_id"],
                            "text": "Updated by the user after dubbing.",
                        }
                    ],
                },
            )
            changed.raise_for_status()
            assert client.get(path + "/localization").json()["audio_outdated"]
            export = client.post(path + "/render", json={"base_version": 6})
            assert export.status_code == 422
            assert "重新配音" in export.text
            versions = client.get(path + "/versions").json()["items"]
            assert [item["version"] for item in versions] == list(range(1, 7))
        finally:
            deleted = client.delete(path)
            assert deleted.status_code == 204
