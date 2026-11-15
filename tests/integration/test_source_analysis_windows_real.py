from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from camcat.media.ffmpeg import extract_audio, extract_thumbnail, measure_visual_quality, probe

pytestmark = [pytest.mark.integration]


def test_source_window_preserves_audio_duration_and_creates_thumbnail(tmp_path: Path) -> None:
    source = Path(os.environ.get("CAMCAT_EXTERNAL_TEST_VIDEO", "/missing-source"))
    if not source.is_file() or not shutil.which("ffmpeg"):
        pytest.skip("CAMCAT_EXTERNAL_TEST_VIDEO and real FFmpeg are required")
    metadata = probe(source)
    if not metadata.has_audio or metadata.duration < 4:
        pytest.skip("a real source with audio and duration >= 4 seconds is required")
    audio = tmp_path / "window.mp3"
    thumbnail = tmp_path / "midpoint.jpg"
    extract_audio(source, audio, start=1, end=3.5)
    extract_thumbnail(source, thumbnail, at=2.25)
    quality = measure_visual_quality(source, start=1, end=3.5)
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(audio)],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    payload = json.loads(result.stdout)
    assert float(payload["format"]["duration"]) == pytest.approx(2.5, abs=0.1)
    assert [stream["codec_type"] for stream in payload["streams"]] == ["audio"]
    assert payload["streams"][0]["sample_rate"] == "16000"
    assert payload["streams"][0]["channels"] == 1
    assert thumbnail.read_bytes().startswith(b"\xff\xd8")
    assert 0.05 <= quality <= 1
