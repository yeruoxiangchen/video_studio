"""Own an official vLLM process using existing local weights/runtime read-only."""

from __future__ import annotations

import json
import os
import secrets
import shlex
import signal
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values


def main() -> None:
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    python = Path(os.environ.get("CAMCAT_VLLM_PYTHON", "/data/zjr/deepsearch_rl/venv/bin/python"))
    model = Path(
        os.environ.get(
            "CAMCAT_TRANSLATION_WEIGHTS", "/data/zjr/enterprise_kb_agent/models/Qwen3-8B"
        )
    )
    if not python.is_file() or not (model / "model.safetensors.index.json").is_file():
        raise SystemExit("Existing vLLM runtime and complete local Qwen3-8B weights are required")
    fraction, minimum = 0.78, 22528
    raw = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,memory.free,memory.total",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    )
    available = []
    for line in raw.splitlines():
        index, uuid, free, total = [part.strip() for part in line.split(",")]
        if int(free) >= minimum:
            available.append((int(free), -int(index), uuid, int(total)))
    if not available:
        raise SystemExit("No GPU has 22528 MiB free; translation service was not launched")
    free, negative_index, gpu_uuid, total = max(available)
    config = root / "config" / "translation.env"
    values = dotenv_values(config) if config.exists() else {}
    origin, served_model = "http://127.0.0.1:18968", "camcat-qwen3-8b"
    key = (
        values.get("CAMCAT_TRANSLATION_API_KEY")
        if values.get("CAMCAT_TRANSLATION_BASE_URL") == origin
        else None
    ) or secrets.token_urlsafe(32)
    descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        for name, value in {"BASE_URL": origin, "API_KEY": key, "MODEL": served_model}.items():
            stream.write(f"export CAMCAT_TRANSLATION_{name}={shlex.quote(value)}\n")
    environment = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": str(-negative_index),
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "VLLM_API_KEY": key,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "VLLM_USAGE_STATS": "0",
        "VLLM_CACHE_ROOT": str(root / "cache" / "vllm"),
        "TRITON_CACHE_DIR": str(root / "cache" / "triton"),
        "TORCHINDUCTOR_CACHE_DIR": str(root / "cache" / "torchinductor"),
    }
    command = [
        str(python),
        "-B",
        "-u",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(model),
        "--served-model-name",
        served_model,
        "--host",
        "127.0.0.1",
        "--port",
        "18968",
        "--dtype",
        "bfloat16",
        "--gpu-memory-utilization",
        str(fraction),
        "--max-model-len",
        "8192",
        "--max-num-seqs",
        "1",
        "--max-num-batched-tokens",
        "2048",
        "--enforce-eager",
        "--disable-log-requests",
    ]
    state = {
        "state": "starting",
        "controller_pid": os.getpid(),
        "gpu_index": -negative_index,
        "gpu_uuid": gpu_uuid,
        "free_mib_at_launch": free,
        "gpu_memory_fraction": fraction,
        "gpu_budget_mib": round(total * fraction),
        "model_path": str(model),
        "runtime_python": str(python),
        "port": 18968,
        "automatic_restart": False,
    }
    status = root / "checks" / "translation-server.json"

    def write_state() -> None:
        state["time"] = datetime.now(UTC).isoformat()
        temporary = status.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        temporary.replace(status)

    write_state()
    process = subprocess.Popen(command, env=environment)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        if process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    state["server_pid"] = process.pid
    write_state()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 1800
        while process.poll() is None and time.monotonic() < deadline:
            try:
                with opener.open(origin + "/health", timeout=2) as response:
                    if response.status == 200:
                        state["state"] = "ready"
                        write_state()
                        break
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(5)
        if state["state"] != "ready" and process.poll() is None:
            state.update(state="failed", error="Local model startup exceeded 1800 seconds")
            process.terminate()
        returncode = process.wait()
        state.update(returncode=returncode)
        if stopping:
            state.update(state="stopped")
        elif state["state"] != "ready" or returncode != 0:
            state.update(state="failed")
        else:
            state.update(state="stopped")
        write_state()
        if not stopping and (returncode or state["state"] == "failed"):
            raise SystemExit(returncode or 1)
    finally:
        if process.poll() is None:
            process.terminate()


if __name__ == "__main__":
    main()
