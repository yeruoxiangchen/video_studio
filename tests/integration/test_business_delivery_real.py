"""Actual CamCat API, local translation/Piper, queue, MinIO and FFmpeg delivery."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_localization_jobs_real import wait_job

pytestmark = [pytest.mark.integration, pytest.mark.external]


@pytest.mark.skipif(
    not os.environ.get("CAMCAT_BUSINESS_SOURCE_JOB"),
    reason="A real analyzed source job is required",
)
def test_real_review_versioning_and_customer_delivery(tmp_path: Path):
    source_job = os.environ["CAMCAT_BUSINESS_SOURCE_JOB"]
    with httpx.Client(
        base_url="http://127.0.0.1:18960",
        timeout=120,
        trust_env=False,
        headers={"X-User-Id": "camcat-local-user"},
    ) as client:
        project = client.post("/api/v1/projects", json={"name": f"业务交付验收-{uuid4()}"})
        project.raise_for_status()
        created = client.post(
            "/api/v1/editing/sessions",
            json={
                "project_id": project.json()["project_id"],
                "source_job_id": source_job,
                "current_goal": "中文旅行口播的英文交付",
            },
        )
        created.raise_for_status()
        session = created.json()
        path = "/api/v1/editing/sessions/" + session["editing_session_id"]
        try:
            initial = client.get(path + "/localization")
            initial.raise_for_status()
            assert initial.json()["delivery"]["state"] == "needs_translation"
            original = next(s for s in session["state"]["sources"] if s["origin"] == "user_upload")
            source = session["state"]["metadata"]["source_media"][0]
            duration = round(float(source["duration_seconds"]) * 1_000_000)
            transcript = "这里几乎都是沙质海滩，游泳很安全，大部分地方都有新西兰圣诞树的树荫。"
            response = client.post(
                path + "/commands",
                json={
                    "base_version": 1,
                    "reason": "Authored source correction for business acceptance",
                    "commands": [
                        {"type": "update_title", "title": "旅行口播英文交付演示"},
                        {
                            "type": "add_track",
                            "track": {
                                "type": "video",
                                "track_id": "main",
                                "name": "旅行口播",
                                "segments": [
                                    {
                                        "type": "video",
                                        "segment_id": "original",
                                        "source_id": original["source_id"],
                                        "timeline_start_us": 0,
                                        "timeline_duration_us": duration,
                                        "source_start_us": 0,
                                        "source_duration_us": duration,
                                    }
                                ],
                            },
                        },
                        {
                            "type": "add_track",
                            "track": {
                                "type": "text",
                                "track_id": "captions",
                                "name": "原文",
                                "segments": [
                                    {
                                        "type": "text",
                                        "segment_id": "source-cue",
                                        "start_us": 0,
                                        "duration_us": duration,
                                        "text": transcript,
                                    }
                                ],
                            },
                        },
                    ],
                },
            )
            response.raise_for_status()
            terms = {"新西兰": "New Zealand"}
            job = client.post(
                path + "/localization/translate",
                json={"base_version": 2, "target_language": "en", "glossary": terms},
            )
            job.raise_for_status()
            translated = wait_job(client, job.json()["job_id"])
            applied = client.post(
                path + f"/localization/{translated['job_id']}/apply", json={"base_version": 2}
            )
            applied.raise_for_status()
            assert client.get(path + "/localization").json()["glossary"] == terms
            job = client.post(
                path + "/localization/dub", json={"base_version": 3, "allow_duration_rewrite": True}
            )
            job.raise_for_status()
            dubbed = wait_job(client, job.json()["job_id"])
            applied = client.post(
                path + f"/localization/{dubbed['job_id']}/apply", json={"base_version": 3}
            )
            applied.raise_for_status()
            assert (
                client.post(
                    path + "/localization/review", json={"base_version": 4, "confirmed": False}
                ).status_code
                == 422
            )
            approved = client.post(
                path + "/localization/review", json={"base_version": 4, "confirmed": True}
            )
            approved.raise_for_status()
            assert approved.json()["state_version"] == 5
            repeated = client.post(
                path + "/localization/review", json={"base_version": 5, "confirmed": True}
            )
            repeated.raise_for_status()
            assert repeated.json()["state_version"] == 5
            render = client.post(
                path + "/render", json={"base_version": 5, "quality_profile": "preview"}
            )
            render.raise_for_status()
            rendered = wait_job(client, render.json()["job_id"])
            route = path + f"/localization/delivery/{rendered['job_id']}?base_version=5"
            archive_response = client.get(route)
            archive_response.raise_for_status()
            assert archive_response.headers["content-type"].startswith("application/zip")
            assert archive_response.headers["cache-control"] == "no-store"
            (tmp_path / "delivery.zip").write_bytes(archive_response.content)
            with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
                assert set(archive.namelist()) == {
                    "video.mp4",
                    "source.srt",
                    "translation.srt",
                    "bilingual.srt",
                    "glossary.csv",
                    "delivery.json",
                }
                assert (
                    hashlib.sha256(archive.read("video.mp4")).hexdigest()
                    == rendered["result"]["output_sha256"]
                )
                assert transcript in archive.read("source.srt").decode()
                assert "New Zealand" in archive.read("translation.srt").decode()
                assert transcript in archive.read("bilingual.srt").decode()
                manifest = json.loads(archive.read("delivery.json"))
                assert manifest["version"] == 5
                assert manifest["project_title"] == "旅行口播英文交付演示"
                assert "storage_key" not in json.dumps(manifest) and "http" not in json.dumps(
                    manifest
                )
                archive.extract("video.mp4", tmp_path)
            metadata = json.loads(
                subprocess.check_output(
                    [
                        str(Path(os.environ["CAMCAT_STUDIO_DATA"]) / "tools/ffmpeg/ffprobe"),
                        "-v",
                        "error",
                        "-show_streams",
                        "-of",
                        "json",
                        str(tmp_path / "video.mp4"),
                    ],
                    text=True,
                )
            )
            assert {s["codec_type"] for s in metadata["streams"]} >= {"video", "audio"}
            # Local-single-user deliberately ignores identity headers. Check the
            # actual resource boundary without pretending it is a multi-user login.
            other = client.post("/api/v1/editing/sessions", json={"current_goal": "隔离校验"})
            other.raise_for_status()
            other_path = "/api/v1/editing/sessions/" + other.json()["editing_session_id"]
            try:
                foreign = client.get(
                    other_path + f"/localization/delivery/{rendered['job_id']}?base_version=1"
                )
                assert foreign.status_code == 404
            finally:
                client.delete(other_path)
            changed = client.post(
                path + "/commands",
                json={
                    "base_version": 5,
                    "reason": "Correct the source after signoff",
                    "commands": [
                        {
                            "type": "update_text_segment",
                            "segment_id": "source-cue",
                            "text": transcript + "请再次核对。",
                        }
                    ],
                },
            )
            changed.raise_for_status()
            context = client.get(path + "/localization").json()
            assert (
                context["translation_outdated"]
                and context["delivery"]["state"] == "needs_translation"
            )
            assert client.get(route).status_code == 409
            assert (
                client.post(path + "/localization/dub", json={"base_version": 6}).status_code == 422
            )
            assert client.post(path + "/render", json={"base_version": 6}).status_code == 422
            (tmp_path / "business-result.json").write_text(
                json.dumps(
                    {
                        "source_job": source_job,
                        "approved_version": 5,
                        "render": rendered,
                        "package_manifest": manifest,
                        "verified_streams": metadata,
                        "foreign_session_status": foreign.status_code,
                        "source_edit_invalidates_delivery": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            client.delete(path)
