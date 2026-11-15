"""Run an unchanged, pinned llama.cpp server on one explicitly selected idle GPU."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--port", type=int, default=18974)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    spec = json.loads((source / "infra/planning-model.lock.json").read_text())
    folder = root / spec["directory"]
    receipt = json.loads((folder / "camcat-source.json").read_text())
    if any(receipt.get(key) != spec[key] for key in ("repository", "revision")):
        raise ValueError("Prepare the pinned planning model first")
    actual_files = {item["file"]: item for item in receipt["files"]}
    for expected in spec["files"]:
        actual = actual_files[expected["file"]]
        path = folder / expected["file"]
        if any(actual.get(k) != expected[k] for k in ("bytes", "sha256")) or (
            path.stat().st_size != expected["bytes"]
            or path.stat().st_mtime_ns != actual["mtime_ns"]
        ):
            raise ValueError("Planning model identity changed; prepare it again")
    runtime = root / spec["runtime_directory"]
    build = json.loads((runtime / "camcat-build.json").read_text())
    if build["revision"] != spec["runtime_revision"] or build["source_modified"]:
        raise ValueError("Native runtime differs from the pinned official source")
    binary = runtime / "bin/llama-server"
    key = dotenv_values(root / "config/local-models.env")["CAMCAT_LOCAL_MODELS_API_KEY"]
    if not key or len(key) < 24:
        raise RuntimeError("The private model gateway key is missing")
    private = root / "config/planning-model.key"
    descriptor = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(key + "\n")
    devices = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,memory.free",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        timeout=10,
    )
    device = next(
        row.split(",") for row in devices.splitlines() if int(row.split(",")[0]) == args.gpu
    )
    _, gpu_uuid, free = (item.strip() for item in device)
    if int(free) < 22528:
        raise RuntimeError("The selected planning GPU must have at least 22528 MiB free")
    state: dict[str, Any] = {
        "state": "starting",
        "phase": "native_model_load",
        "pid": os.getpid(),
        "gpu_index": args.gpu,
        "gpu_uuid": gpu_uuid,
        "budget_mib": spec["gpu_budget_mib"],
        "model": spec["model_alias"],
        "revision": spec["revision"],
        "precision": "Q4_K_M",
        "runtime_revision": spec["runtime_revision"],
        "source_modified": False,
        "automatic_restart": False,
        "port": args.port,
        "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    status = root / "checks/planning-model-server.json"

    def save() -> None:
        state["time"] = datetime.now(UTC).isoformat()
        pending = status.with_suffix(".tmp")
        pending.write_text(json.dumps(state, indent=2) + "\n")
        pending.replace(status)

    command = [
        str(binary),
        "--model",
        str(folder / spec["files"][0]["file"]),
        "--mmproj",
        str(folder / spec["files"][1]["file"]),
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--alias",
        spec["model_alias"],
        "--api-key-file",
        str(private),
        "--ctx-size",
        str(spec["context_tokens"]),
        "--parallel",
        "1",
        "--n-gpu-layers",
        "999",
        "--flash-attn",
        "on",
        "--fit-target",
        "3584",
        "--threads",
        "6",
        "--threads-batch",
        "6",
        "--n-predict",
        "4096",
        "--reasoning-budget",
        str(spec["reasoning_tokens"]),
        "--jinja",
        "--no-webui",
        "--image-max-tokens",
        "512",
        "--timeout",
        "300",
    ]
    environment = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": str(args.gpu),
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "LD_LIBRARY_PATH": str(runtime / "bin") + ":/home/zjr/cuda-12.1/lib64",
    }
    stopped = False
    process: subprocess.Popen[bytes] | None = None

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True
        if process is not None and process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    save()
    try:
        process = subprocess.Popen(command, env=environment)
        state["server_pid"] = process.pid
        save()
        deadline = time.monotonic() + 1800
        with httpx.Client(
            trust_env=False, timeout=3, headers={"Authorization": "Bearer " + key}
        ) as client:
            while not stopped:
                if process.poll() is not None:
                    raise RuntimeError("Native planning model exited " + str(process.returncode))
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
                    pid, used = (part.strip() for part in row.split(",", 1))
                    if pid == str(process.pid) and used.isdigit():
                        state["peak_used_mib"] = max(state.get("peak_used_mib", 0), int(used))
                        if int(used) > spec["gpu_budget_mib"]:
                            raise RuntimeError("Native planning model exceeded its GPU budget")
                if state["state"] != "ready":
                    try:
                        health = client.get(f"http://127.0.0.1:{args.port}/health")
                        if health.is_success and health.json().get("status") == "ok":
                            models = client.get(f"http://127.0.0.1:{args.port}/v1/models").json()
                            if any(m["id"] == spec["model_alias"] for m in models["data"]):
                                state.update(state="ready", phase="ready")
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        raise TimeoutError("Native planning model startup deadline")
                save()
                time.sleep(5)
        state.update(state="stopped", phase="stopped")
    except Exception as error:
        state.update(
            state="failed", phase="failed", error_type=type(error).__name__, error=str(error)
        )
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        save()


if __name__ == "__main__":
    main()
