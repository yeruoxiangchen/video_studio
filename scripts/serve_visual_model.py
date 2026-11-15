"""Start the pinned visual model on one explicitly selected idle GPU, without restart."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--port", type=int, default=18972)
    args = parser.parse_args()
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source))
    from dotenv import dotenv_values

    private = dotenv_values(root / "config/local-models.env")
    if "CAMCAT_LOCAL_MODELS_API_KEY" in private:
        os.environ["CAMCAT_LOCAL_MODELS_API_KEY"] = str(private["CAMCAT_LOCAL_MODELS_API_KEY"])
    if len(os.environ.get("CAMCAT_LOCAL_MODELS_API_KEY", "")) < 24:
        raise RuntimeError("Local visual gateway key is missing")
    state = {
        "state": "starting",
        "phase": "gpu_preflight",
        "pid": os.getpid(),
        "automatic_restart": False,
        "port": args.port,
        "gpu_index": args.gpu,
        "time": datetime.now(UTC).isoformat(),
    }
    status = root / "checks/visual-model-server.json"
    status.write_text(json.dumps(state, indent=2) + "\n")
    try:
        devices = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=10,
        )
        selected = next(
            row.split(",") for row in devices.splitlines() if int(row.split(",")[0]) == args.gpu
        )
        _, gpu_uuid, free, total = (item.strip() for item in selected)
        if int(free) < 22528:
            raise RuntimeError("Selected GPU does not have enough idle memory")
        state.update(gpu_uuid=gpu_uuid, free_mib=int(free), budget_mib=int(int(total) * 0.86))
        status.write_text(json.dumps(state, indent=2) + "\n")
        os.environ.update(
            CUDA_VISIBLE_DEVICES=str(args.gpu),
            CUDA_DEVICE_ORDER="PCI_BUS_ID",
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            TOKENIZERS_PARALLELISM="false",
        )
        import uvicorn

        uvicorn.run(
            "services.local_models.visual_server:app",
            host="127.0.0.1",
            port=args.port,
            workers=1,
            access_log=False,
        )
    except BaseException as error:
        state = json.loads(status.read_text())
        state.update(
            state="failed", error_type=type(error).__name__, time=datetime.now(UTC).isoformat()
        )
        status.write_text(json.dumps(state, indent=2) + "\n")
        raise


if __name__ == "__main__":
    main()
