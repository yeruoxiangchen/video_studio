from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.skipif(
        os.environ.get("CAMCAT_LOCAL_MODELS_EXTERNAL") != "1",
        reason="requires the real Compose API, worker and local model services",
    ),
]


def test_binary_mime_licensed_video_is_imported_and_indexed() -> None:
    source = Path(__file__).resolve().parents[2]
    item = json.loads((source / "docs/open-video-library.json").read_text())[0]
    payload = {key: item[key] for key in ("download_url", "source_url", "license_name", "filename")}
    with httpx.Client(
        base_url=os.environ["CAMCAT_TEST_API_URL"], timeout=90, trust_env=False
    ) as client:
        response = client.post("/api/v1/videos/import", json=payload)
        assert response.status_code == 202, response.text
        imported = response.json()
        assert imported["job_id"]
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            response = client.get(f"/api/v1/jobs/{imported['job_id']}")
            assert response.status_code == 200
            job = response.json()
            if job["status"] == "succeeded":
                repeated = client.post("/api/v1/videos/import", json=payload)
                assert repeated.status_code == 202
                ready = repeated.json()
                assert ready["video_id"] == imported["video_id"]
                assert ready["status"] == "ready"
                assert ready["segment_count"] > 0
                return
            assert job["status"] not in {"failed", "dead_letter", "cancelled"}, job["error"]
            time.sleep(1)
    pytest.fail("Licensed video indexing did not complete within 300 seconds")


def test_licensed_audio_catalog_contains_real_media_durations(tmp_path: Path) -> None:
    from camcat.config import get_settings
    from camcat.rendering.fingerprint import probe_json
    from camcat.services.object_store import ObjectStore

    store = ObjectStore(get_settings())
    catalog = store.read_json("library/audio/catalog.json", default=[])
    assert catalog
    for index, item in enumerate(catalog):
        assert item.get("duration_us", 0) > 0, f"Missing measured duration for {item['id']}"
        target = tmp_path / f"audio-{index}.mp3"
        store.download_file(item["storage_key"], target)
        metadata = probe_json(target)
        assert any(stream.get("codec_type") == "audio" for stream in metadata["streams"])
        actual = round(float(metadata["format"]["duration"]) * 1_000_000)
        assert item["duration_us"] == actual
