"""Cancel a real queued render after its FFmpeg child has actually started."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.external]


@pytest.mark.skipif(
    not os.environ.get("CAMCAT_RENDER_CANCEL_SOURCE_JOB"),
    reason="a real unexpired source analysis job is required",
)
def test_running_render_cancel_releases_encoder_and_preserves_cancelled_state(tmp_path: Path):
    source_job = os.environ["CAMCAT_RENDER_CANCEL_SOURCE_JOB"]
    source_root = Path(__file__).resolve().parents[2]
    data = Path(os.environ.get("CAMCAT_STUDIO_DATA", "/data/zjr/camcat_studio"))
    job_id = session_id = ""
    with httpx.Client(
        base_url=os.environ.get("CAMCAT_E2E_API_BASE", "http://127.0.0.1:18960"),
        trust_env=False,
        timeout=30,
    ) as client:

        def post(path, payload):
            response = client.post("/api/v1" + path, json=payload)
            assert response.is_success, response.text
            return response.json()

        try:
            project = post("/projects", {"name": "Actual encoder cancellation " + uuid4().hex[:8]})
            session = post(
                "/editing/sessions",
                {
                    "project_id": project["project_id"],
                    "source_job_id": source_job,
                    "current_goal": "真实渲染取消检查",
                },
            )
            session_id = session["editing_session_id"]
            session_path = "/editing/sessions/" + session_id
            prepared = post(session_path + "/localization/prepare", {"base_version": 1})
            job = post(
                session_path + "/render",
                {"base_version": prepared["state_version"], "quality_profile": "high"},
            )
            job_id = job["job_id"]
            receipt_path = data / "runtime/jobs" / job_id / "renderer-process.json"
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if receipt_path.is_file():
                    receipt = json.loads(receipt_path.read_text())
                    if receipt["state"] == "running":
                        break
                state = client.get("/api/v1/jobs/" + job_id).json()
                assert state["status"] in {"queued", "running"}, state
                time.sleep(0.1)
            else:
                pytest.fail("the actual encoder did not expose its running process identity")
            assert receipt["pid"] is not None
            started = time.monotonic()
            post("/jobs/" + job_id + "/cancel", {})
            deadline = started + 8
            while time.monotonic() < deadline:
                state = client.get("/api/v1/jobs/" + job_id).json()
                receipt = json.loads(receipt_path.read_text())
                if state["status"] == "cancelled" and receipt["state"] == "interrupted":
                    break
                time.sleep(0.1)
            else:
                pytest.fail("cancellation did not stop the actual encoder within eight seconds")
            cancellation_seconds = time.monotonic() - started
            assert receipt["returncode"] is not None
            assert not (receipt_path.parent / "camcat-render.mp4").exists()
            code = (
                "import os;\ntry: os.kill(" + str(receipt["pid"]) + ", 0)\n"
                "except ProcessLookupError: raise SystemExit(0)\nraise SystemExit(1)"
            )
            subprocess.run(
                [
                    "bash",
                    "scripts/studio.sh",
                    "compose",
                    "exec",
                    "-T",
                    "render-worker",
                    "python",
                    "-B",
                    "-c",
                    code,
                ],
                cwd=source_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            time.sleep(1)
            final = client.get("/api/v1/jobs/" + job_id).json()
            assert final["status"] == "cancelled"
            assert final["attempts"] == 1
            (tmp_path / "cancellation.json").write_text(
                json.dumps(
                    {
                        "state": "passed",
                        "job_id": job_id,
                        "cancel_to_reaped_seconds": round(cancellation_seconds, 3),
                        "no_partial_output": True,
                        "encoder_process_gone": True,
                        "job_remains_cancelled": True,
                        "receipt": receipt,
                    },
                    indent=2,
                )
                + "\n"
            )
        finally:
            if job_id:
                state = client.get("/api/v1/jobs/" + job_id).json()
                if state.get("status") in {"queued", "running"}:
                    client.post("/api/v1/jobs/" + job_id + "/cancel")
            if session_id:
                client.delete("/api/v1/editing/sessions/" + session_id)
