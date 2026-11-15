"""Preflight resources, then serve real models; failures terminate without restart."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source))
    from services.local_models.resources import gpu_plan_with_grace

    visual_precision = os.environ.get("CAMCAT_LOCAL_VISUAL_PRECISION", "nf4")

    vendor = source / "third_party/qwen3_vl"
    receipt = json.loads((vendor / "UPSTREAM.json").read_text())
    for name, expected in receipt["files"].items():
        if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Official Qwen source was modified: " + name)
    lock = json.loads((source / "infra/local-models.lock.json").read_text())
    asr = root / "models" / lock["asr"]["repository"].rsplit("/", 1)[1]
    if not (root / "asr-venv/bin/python").is_file() or not (asr / "model.bin").is_file():
        raise RuntimeError("Run bash scripts/studio.sh prepare-asr before starting local models")
    asr_source = json.loads((asr / "camcat-source.json").read_text())
    if any(asr_source.get(key) != lock["asr"][key] for key in ("repository", "revision")):
        raise RuntimeError("Local multilingual ASR differs from the pinned source")
    for role in ("embedding", "reranker"):
        folder = root / "models" / lock[role]["repository"].split("/")[-1]
        actual = json.loads((folder / "camcat-source.json").read_text())
        if actual["revision"] != lock[role]["revision"]:
            raise RuntimeError("Local model revision differs from the pinned source")
    available = []
    asr_device = os.environ.get("CAMCAT_ASR_DEVICE", "cpu")
    asr_gpu = int(os.environ.get("CAMCAT_ASR_GPU_INDEX", "-1")) if asr_device == "cuda" else None

    def read_devices():
        nonlocal available
        raw = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=10,
        )
        available = []
        for line in raw.splitlines():
            index, uuid, free, total = [part.strip() for part in line.split(",")]
            if int(free) >= 22528 and int(index) != asr_gpu:
                available.append(
                    {
                        "index": int(index),
                        "uuid": uuid,
                        "free_mib": int(free),
                        "total_mib": int(total),
                    }
                )
        return available

    (root / "checks/local-models-server.json").write_text(
        json.dumps(
            {
                "state": "starting",
                "phase": "gpu_preflight",
                "pid": os.getpid(),
                "time": datetime.now(UTC).isoformat(),
                "automatic_restart": False,
            },
            indent=2,
        )
        + "\n"
    )
    try:
        if asr_gpu is not None:
            from services.local_models.asr_resources import asr_execution_plan

            asr_free = int(
                subprocess.check_output(
                    [
                        "nvidia-smi",
                        "-i",
                        str(asr_gpu),
                        "--query-gpu=memory.free",
                        "--format=csv,noheader,nounits",
                    ],
                    text=True,
                    timeout=10,
                ).strip()
            )
            asr_execution_plan(asr_device, asr_gpu, asr_free, set())
        preferred = os.environ.get("CAMCAT_LOCAL_GPU_INDICES", "")
        roles = gpu_plan_with_grace(
            read_devices,
            preferred_indices=(
                [int(index) for index in preferred.split(",")] if preferred else None
            ),
            visual_precision=visual_precision,
        )
    except Exception as error:
        (root / "checks/local-models-server.json").write_text(
            json.dumps(
                {
                    "state": "failed",
                    "phase": "gpu_preflight",
                    "pid": os.getpid(),
                    "time": datetime.now(UTC).isoformat(),
                    "required_idle_gpus": 3 if visual_precision == "bfloat16" else 2,
                    "available_idle_gpus": len(available),
                    "error_type": type(error).__name__,
                    "automatic_restart": False,
                },
                indent=2,
            )
            + "\n"
        )
        raise
    os.environ.update(
        CUDA_VISIBLE_DEVICES=",".join(
            str(index) for index in dict.fromkeys(row["index"] for row in roles)
        ),
        CUDA_DEVICE_ORDER="PCI_BUS_ID",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
    )
    sys.path[:0] = [str(source), str(vendor)]
    status = root / "checks/local-models-server.json"
    state = {
        "state": "starting",
        "phase": "import_runtime",
        "pid": os.getpid(),
        "time": datetime.now(UTC).isoformat(),
        "gpus": roles,
        "visual_precision": visual_precision,
        "source_sha256": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in (
                "scripts/serve_local_models.py",
                "services/local_models/runtime.py",
                "services/local_models/server.py",
                "services/local_models/generation.py",
                "services/local_models/validation.py",
                "services/local_models/protocol.py",
                "services/local_models/resources.py",
                "services/local_models/asr_worker.py",
                "services/local_models/asr_contract.py",
                "services/local_models/asr_resources.py",
            )
        },
        "port": 18969,
        "automatic_restart": False,
    }
    status.write_text(json.dumps(state, indent=2) + "\n")
    try:
        import uvicorn

        uvicorn.run(
            "services.local_models.server:app",
            host="127.0.0.1",
            port=18969,
            workers=1,
            access_log=False,
            log_level="info",
        )
    except BaseException as error:
        state = json.loads(status.read_text())
        state.update(
            state="failed", error_type=type(error).__name__, time=datetime.now(UTC).isoformat()
        )
        status.write_text(json.dumps(state, indent=2) + "\n")
        raise
    else:
        state = json.loads(status.read_text())
        was_ready = state["state"] == "ready"
        state.update(state="stopped" if was_ready else "failed", time=datetime.now(UTC).isoformat())
        status.write_text(json.dumps(state, indent=2) + "\n")
        if not was_ready:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
