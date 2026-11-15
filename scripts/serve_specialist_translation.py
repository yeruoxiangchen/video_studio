"""Run the pinned official translation model through the unmodified native server."""

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


def execution_plan(*, gpu_index: int, free_mib: int, total_mib: int) -> dict[str, int]:
    budget = 4096
    if (
        gpu_index < 0
        or total_mib <= 0
        or free_mib < budget + 1024
        or free_mib > total_mib
        or total_mib - free_mib + budget > int(total_mib * 0.86)
    ):
        raise ValueError(
            "Translation requires an explicit GPU and a bounded combined memory budget"
        )
    return {"gpu_index": gpu_index, "gpu_budget_mib": budget}


def validate_runtime(receipt: dict[str, Any], revision: str) -> None:
    if receipt.get("revision") != revision or receipt.get("source_modified") is not False:
        raise ValueError("Translation runtime differs from the pinned official build")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--port", type=int, default=18976)
    args = parser.parse_args()
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    source = Path(__file__).resolve().parents[1]
    lock = json.loads((source / "infra/specialist-translation.lock.json").read_text())
    weights = root / lock["directory"]
    identity = json.loads((weights / "camcat-source.json").read_text())
    for key in ("repository", "revision", "file", "bytes", "sha256"):
        if identity.get(key) != lock[key]:
            raise ValueError("Prepare the pinned translation weights before starting the service")
    model = weights / lock["file"]
    if model.stat().st_size != lock["bytes"] or model.stat().st_mtime_ns != identity["mtime_ns"]:
        raise ValueError("Translation weight identity changed; verify the source checksum again")
    runtime = root / lock["runtime_directory"]
    validate_runtime(
        json.loads((runtime / "camcat-build.json").read_text()), lock["runtime_revision"]
    )
    raw = subprocess.check_output(
        [
            "nvidia-smi",
            "-i",
            str(args.gpu),
            "--query-gpu=uuid,memory.free,memory.total",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        timeout=10,
    )
    uuid, free, total = [part.strip() for part in raw.split(",")]
    plan = execution_plan(gpu_index=args.gpu, free_mib=int(free), total_mib=int(total))
    keyfile = root / "config/planning-model.key"
    key = keyfile.read_text().strip()
    if len(key) < 24:
        raise ValueError("The private translation gateway key is missing")
    state: dict[str, Any] = {
        "state": "starting",
        "phase": "model_load",
        "pid": os.getpid(),
        **plan,
        "gpu_uuid": uuid,
        "model": lock["model_alias"],
        "revision": lock["revision"],
        "precision": "Q8_0",
        "runtime_revision": lock["runtime_revision"],
        "automatic_restart": False,
        "port": args.port,
        "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    path = root / "checks/specialist-translation-server.json"

    def save() -> None:
        state["time"] = datetime.now(UTC).isoformat()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        temporary.replace(path)

    process: subprocess.Popen[bytes] | None = None
    stopped = False

    def stop(_number: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True
        if process is not None and process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    save()
    command = [
        str(runtime / "bin/llama-server"),
        "--model",
        str(model),
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--alias",
        lock["model_alias"],
        "--api-key-file",
        str(keyfile),
        "--ctx-size",
        str(lock["context_tokens"]),
        "--parallel",
        "1",
        "--n-gpu-layers",
        "999",
        "--flash-attn",
        "on",
        "--threads",
        "4",
        "--threads-batch",
        "4",
        "--n-predict",
        "2048",
        "--jinja",
        "--no-webui",
        "--timeout",
        "180",
    ]
    environment = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": str(args.gpu),
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "LD_LIBRARY_PATH": str(runtime / "bin") + ":/home/zjr/cuda-12.1/lib64",
    }
    try:
        with (root / "logs/specialist-translation-server.log").open("a") as log:
            process = subprocess.Popen(command, env=environment, stdout=log, stderr=log)
            state["server_pid"] = process.pid
            save()
            deadline = time.monotonic() + 300
            with httpx.Client(
                base_url=f"http://127.0.0.1:{args.port}",
                trust_env=False,
                timeout=5,
                headers={"Authorization": "Bearer " + key},
            ) as client:
                while not stopped:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Translation model process exited; inspect its server log"
                        )
                    rows = subprocess.check_output(
                        [
                            "nvidia-smi",
                            "--query-compute-apps=pid,used_gpu_memory",
                            "--format=csv,noheader,nounits",
                        ],
                        text=True,
                        timeout=10,
                    )
                    used = sum(
                        int(row.split(",")[1])
                        for row in rows.splitlines()
                        if int(row.split(",")[0]) == process.pid
                    )
                    state["gpu_peak_memory_mib"] = max(state.get("gpu_peak_memory_mib", 0), used)
                    if used > plan["gpu_budget_mib"]:
                        raise RuntimeError("Translation exceeded its 4 GiB GPU memory budget")
                    if state["state"] != "ready":
                        try:
                            if client.get("/health").status_code == 200:
                                state.update(state="ready", phase="serving")
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline:
                            raise RuntimeError("Translation model startup timed out")
                    save()
                    time.sleep(2)
        state.update(state="stopped", phase="stopped")
    except Exception as error:
        state.update(state="failed", phase="failed", error=str(error))
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
