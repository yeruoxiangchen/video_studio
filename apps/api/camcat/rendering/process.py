"""Bounded encoder process groups, with live job heartbeats and guaranteed reaping."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path


class EncoderTimeout(TimeoutError):
    pass


class EncoderFailed(RuntimeError):
    pass


def _stop(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    if process.poll() is not None:
        process.wait()
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def run_encoder(
    args: list[str],
    *,
    stderr: Path,
    record: Path,
    heartbeat: Callable[[], None] | None = None,
    timeout_seconds: float = 900,
    poll_seconds: float = 0.2,
    heartbeat_seconds: float = 2.0,
    grace_seconds: float = 2.0,
) -> None:
    if timeout_seconds <= 0 or poll_seconds <= 0 or heartbeat_seconds <= 0:
        raise ValueError("encoder time budgets must be positive")
    stderr.parent.mkdir(parents=True, exist_ok=True)
    record.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    state = "interrupted"
    reason: str | None = None

    def save(current: str) -> None:
        temporary = record.with_suffix(record.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "state": current,
                    "error_type": reason,
                    "pid": process.pid if process else None,
                    "returncode": process.returncode if process else None,
                    "elapsed_seconds": round(time.monotonic() - started, 6),
                    "process_group_owned": process is not None,
                },
                indent=2,
            )
            + "\n"
        )
        temporary.replace(record)

    try:
        if heartbeat:
            heartbeat()
        with stderr.open("wb") as log:
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                start_new_session=True,
            )
            save("running")
            next_heartbeat = started + heartbeat_seconds
            while process.poll() is None:
                now = time.monotonic()
                if now - started >= timeout_seconds:
                    raise EncoderTimeout("FFmpeg render exceeded its execution time budget")
                if heartbeat and now >= next_heartbeat:
                    heartbeat()
                    next_heartbeat = now + heartbeat_seconds
                try:
                    process.wait(
                        timeout=min(poll_seconds, max(0.001, timeout_seconds - (now - started)))
                    )
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                raise EncoderFailed(f"FFmpeg encoder exited {process.returncode}")
            if heartbeat:
                heartbeat()
            state = "completed"
    except BaseException as error:
        reason = type(error).__name__
        raise
    finally:
        if process is not None:
            _stop(process, grace_seconds)
        save(state)
