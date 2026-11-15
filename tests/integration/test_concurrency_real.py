"""Concurrent requests against the real API, database and persistent queue."""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import httpx
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CAMCAT_LOCALIZATION_EXTERNAL") != "1",
        reason="requires the actual CamCat API, Postgres and worker",
    ),
]


@pytest.fixture
def actual_session() -> Iterator[tuple[httpx.Client, str]]:
    with httpx.Client(
        base_url=os.environ.get("CAMCAT_TEST_API_URL", "http://127.0.0.1:18960"),
        timeout=45,
        trust_env=False,
    ) as client:
        project = client.post("/api/v1/projects", json={"name": f"并发回归-{uuid4()}"})
        project.raise_for_status()
        created = client.post(
            "/api/v1/editing/sessions",
            json={"project_id": project.json()["project_id"], "current_goal": "并发回归"},
        )
        created.raise_for_status()
        path = "/api/v1/editing/sessions/" + created.json()["editing_session_id"]
        try:
            yield client, path
        finally:
            context = client.get(path + "/localization")
            if context.is_success:
                for job in context.json()["jobs"]:
                    if job["status"] in {"queued", "running"}:
                        client.post(f"/api/v1/jobs/{job['job_id']}/cancel")
            assert client.delete(path).status_code == 204


def test_concurrent_same_version_commands_accept_one_writer(
    actual_session: tuple[httpx.Client, str],
) -> None:
    client, path = actual_session
    barrier = Barrier(8)

    def write(index: int) -> httpx.Response:
        barrier.wait(timeout=15)
        return client.post(
            path + "/commands",
            json={
                "base_version": 1,
                "commands": [{"type": "update_goal", "goal": f"writer-{index}"}],
                "reason": "Actual simultaneous compare-and-swap requests",
            },
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(write, range(8)))
    assert Counter(response.status_code for response in responses) == {200: 1, 409: 7}
    winner = next(response.json() for response in responses if response.status_code == 200)
    current = client.get(path).json()
    assert current["state_version"] == 2
    assert current["state"]["goal"] == winner["state"]["goal"]
    versions = client.get(path + "/versions").json()["items"]
    assert [version["version"] for version in versions] == [1, 2]


def test_concurrent_translation_submissions_return_one_job(
    actual_session: tuple[httpx.Client, str],
) -> None:
    client, path = actual_session
    edited = client.post(
        path + "/commands",
        json={
            "base_version": 1,
            "reason": "Test-authored source subtitle for queue concurrency",
            "commands": [
                {
                    "type": "add_track",
                    "track": {
                        "type": "text",
                        "track_id": "captions",
                        "name": "并发测试字幕",
                        "segments": [
                            {
                                "type": "text",
                                "segment_id": "concurrent-cue",
                                "start_us": 0,
                                "duration_us": 8_000_000,
                                "text": "欢迎使用 CamCat，分享你的故事。",
                            }
                        ],
                    },
                }
            ],
        },
    )
    edited.raise_for_status()
    barrier = Barrier(8)

    def submit(_index: int) -> httpx.Response:
        barrier.wait(timeout=15)
        return client.post(
            path + "/localization/translate",
            json={"base_version": 2, "target_language": "en"},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(submit, range(8)))
    assert Counter(response.status_code for response in responses) == {202: 8}
    identifiers = {response.json()["job_id"] for response in responses}
    assert len(identifiers) == 1
    context = client.get(path + "/localization").json()
    assert {job["job_id"] for job in context["jobs"]} == identifiers
    assert client.get(path).json()["state_version"] == 2


def test_web_rate_limit_has_retryable_json_and_recovers() -> None:
    with httpx.Client(
        base_url=os.environ.get("CAMCAT_TEST_WEB_URL", "http://127.0.0.1:18962"),
        timeout=30,
        trust_env=False,
        limits=httpx.Limits(max_connections=32),
    ) as client:
        time.sleep(2.5)
        with ThreadPoolExecutor(max_workers=32) as pool:
            responses = list(pool.map(lambda _: client.get("/api/v1/projects?limit=1"), range(128)))
        statuses = Counter(response.status_code for response in responses)
        assert set(statuses) <= {200, 429}, statuses
        limited = [response for response in responses if response.status_code == 429]
        assert limited, statuses
        for response in limited:
            assert int(response.headers["Retry-After"]) >= 1
            error = response.json()["error"]
            assert error["code"] == "rate_limited"
            assert error["request_id"]
        time.sleep(3)
        recovered = client.get("/api/v1/projects?limit=1")
        assert recovered.status_code == 200


@pytest.mark.parametrize(
    "commands",
    [
        [{"type": "update_text_segment", "segment_id": "missing", "text": "Invalid target"}],
        [
            {"type": "add_track", "track": {"type": "text", "track_id": "duplicate", "name": "A"}},
            {"type": "add_track", "track": {"type": "text", "track_id": "duplicate", "name": "B"}},
        ],
        [{"type": "update_goal", "goal": "并发回归"}],
    ],
    ids=["unknown-segment", "duplicate-track-in-batch", "no-change"],
)
def test_invalid_domain_commands_return_422_without_partial_changes(
    actual_session: tuple[httpx.Client, str], commands: list[dict]
) -> None:
    client, path = actual_session
    before = client.get(path).json()
    rejected = client.post(
        path + "/commands",
        json={"base_version": 1, "commands": commands, "reason": "Invalid-domain-input check"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["request_id"]
    after = client.get(path).json()
    assert after["state_version"] == before["state_version"] == 1
    assert after["state"] == before["state"]
    assert client.get(path + "/patches").json()["items"] == []
