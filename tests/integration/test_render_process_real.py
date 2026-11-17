"""Encoder lifecycle checks use an actual paced FFmpeg process."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from camcat.rendering.process import EncoderTimeout, run_encoder

pytestmark = pytest.mark.integration


def test_encoder_exposes_its_live_owned_pid_before_cancellation(tmp_path: Path):
    cancel = Event()
    record = tmp_path / "live.json"

    def heartbeat():
        if cancel.is_set():
            raise RuntimeError("job cancellation requested")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_encoder,
            paced(tmp_path / "live.mp4"),
            stderr=tmp_path / "error.log",
            record=record,
            heartbeat=heartbeat,
            heartbeat_seconds=0.1,
            timeout_seconds=10,
        )
        try:
            deadline = time.monotonic() + 2
            while not record.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert record.is_file(), "the active encoder must expose its owned process identity"
            data = json.loads(record.read_text())
            assert data["state"] == "running"
            assert data["returncode"] is None
            os.kill(data["pid"], 0)
        finally:
            cancel.set()
            with pytest.raises(RuntimeError, match="cancellation requested"):
                future.result(timeout=5)
    assert json.loads(record.read_text())["state"] == "interrupted"


def paced(output):
    return [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=160x90:r=10:d=20",
        "-c:v",
        "libx264",
        "-threads",
        "1",
        "-y",
        str(output),
    ]


def test_encoder_timeout_reaps_its_process(tmp_path: Path):
    record = tmp_path / "process.json"
    with pytest.raises(EncoderTimeout):
        run_encoder(
            paced(tmp_path / "timeout.mp4"),
            stderr=tmp_path / "error.log",
            record=record,
            timeout_seconds=0.4,
            poll_seconds=0.05,
        )
    data = json.loads(record.read_text())
    assert data["state"] == "interrupted"
    assert data["returncode"] is not None
    with pytest.raises(ProcessLookupError):
        os.kill(data["pid"], 0)


def test_encoder_callback_cancellation_reaps_its_process(tmp_path: Path):
    calls = 0

    def heartbeat():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("job cancellation requested")

    record = tmp_path / "process.json"
    with pytest.raises(RuntimeError, match="cancellation requested"):
        run_encoder(
            paced(tmp_path / "cancel.mp4"),
            stderr=tmp_path / "error.log",
            record=record,
            heartbeat=heartbeat,
            timeout_seconds=10,
            poll_seconds=0.05,
            heartbeat_seconds=0.1,
        )
    data = json.loads(record.read_text())
    assert data["returncode"] is not None
    with pytest.raises(ProcessLookupError):
        os.kill(data["pid"], 0)


def test_encoder_completes_and_records_a_real_output(tmp_path: Path):
    output = tmp_path / "done.mp4"
    args = paced(output)
    args.remove("-re")
    args[args.index("color=c=blue:s=160x90:r=10:d=20")] = "color=c=blue:s=160x90:r=10:d=0.5"
    record = tmp_path / "process.json"
    run_encoder(args, stderr=tmp_path / "error.log", record=record, timeout_seconds=10)
    assert output.stat().st_size > 0
    assert json.loads(record.read_text())["state"] == "completed"
