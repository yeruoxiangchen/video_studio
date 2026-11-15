"""Persistent multilingual faster-whisper CPU worker; private JSON-lines transport."""

from __future__ import annotations

import contextlib
import importlib.metadata
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .asr_contract import ASR_MODEL, transcript_packet
from .asr_resources import asr_execution_plan

PREFIX = "CAMCAT_ASR_JSON "


def send(payload: dict[str, Any]) -> None:
    print(PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> None:
    device = os.environ.get("CAMCAT_ASR_DEVICE", "cpu")
    gpu_index = int(os.environ.get("CAMCAT_ASR_GPU_INDEX", "-1")) if device == "cuda" else None
    reserved = {int(x) for x in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",") if x}
    free = (
        int(
            subprocess.check_output(
                [
                    "nvidia-smi",
                    "-i",
                    str(gpu_index),
                    "--query-gpu=memory.free",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                timeout=10,
            ).strip()
        )
        if device == "cuda"
        else None
    )
    plan = asr_execution_plan(device, gpu_index, free, reserved)
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_index) if device == "cuda" else ""
    from av.error import FFmpegError
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio

    runtime = Path(os.environ["CAMCAT_STUDIO_DATA"]).resolve()
    weights = runtime / "models" / ASR_MODEL.rsplit("/", 1)[1]
    peak = 0

    def memory_check() -> None:
        nonlocal peak
        if device != "cuda":
            return
        rows = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=10,
        )
        for row in rows.splitlines():
            pid, memory = [part.strip() for part in row.split(",")]
            if pid == str(os.getpid()):
                peak = max(peak, int(memory))
        if peak > plan["memory_budget_mib"]:
            send({"event": "error", "code": "asr_memory_budget_exceeded"})
            raise SystemExit(1)

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            model = WhisperModel(
                str(weights),
                device=plan["device"],
                compute_type=plan["compute_type"],
                cpu_threads=plan["cpu_threads"],
                num_workers=1,
                local_files_only=True,
            )
        source = json.loads((weights / "camcat-source.json").read_text())
        memory_check()
        send(
            {
                "event": "ready",
                "model": ASR_MODEL,
                "model_revision": source["revision"],
                **plan,
                "runtime_version": importlib.metadata.version("faster-whisper"),
                "decoder_version": importlib.metadata.version("av"),
            }
        )
    except Exception as error:
        send({"event": "error", "error_type": type(error).__name__})
        raise SystemExit(1) from None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            path = Path(request["path"]).resolve(strict=True)
            if not path.is_relative_to(runtime):
                raise ValueError("ASR input must be a project temporary file")
            try:
                audio = decode_audio(str(path), sampling_rate=16000)
            except (ValueError, FFmpegError):
                send(
                    {
                        "event": "error",
                        "request_id": request["request_id"],
                        "code": "asr_invalid_input",
                        "error_type": "InvalidAudio",
                    }
                )
                continue
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                segments, info = model.transcribe(
                    audio,
                    language=None,
                    beam_size=5,
                    vad_filter=True,
                    word_timestamps=True,
                    condition_on_previous_text=False,
                )
                result = transcript_packet(segments, info)
                memory_check()
                result["runtime"] = {**plan, "gpu_peak_memory_mib": peak}
            send(
                {
                    "event": "result",
                    "request_id": request["request_id"],
                    **result,
                }
            )
        except Exception as error:
            send({"event": "error", "error_type": type(error).__name__})


if __name__ == "__main__":
    main()
