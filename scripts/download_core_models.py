"""Download the real pinned canonical Qwen models to this project's data directory."""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    from huggingface_hub import hf_hub_download, snapshot_download

    source = Path(__file__).resolve().parents[1]
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    lock = json.loads((source / "infra/local-models.lock.json").read_text())
    status = root / "checks/model-download.json"
    state: dict = {
        "state": "running",
        "pid": os.getpid(),
        "automatic_restart": False,
        "completed_models": [],
    }

    def save() -> None:
        state["time"] = datetime.now(UTC).isoformat()
        temporary = status.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        temporary.replace(status)

    try:
        for role in ("embedding", "reranker"):
            spec = lock[role]
            folder = root / "models" / spec["repository"].split("/")[-1]
            folder.mkdir(parents=True, exist_ok=True)
            state.update(
                phase="download_" + role,
                model=spec["repository"],
                revision=spec["revision"],
                folder=str(folder),
                downloaded_bytes=0,
            )
            save()
            index = hf_hub_download(
                spec["repository"],
                "model.safetensors.index.json",
                revision=spec["revision"],
                local_dir=folder,
                token=False,
            )
            weights = json.loads(Path(index).read_text())
            state["expected_weight_bytes"] = weights["metadata"]["total_size"]
            save()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    snapshot_download,
                    repo_id=spec["repository"],
                    revision=spec["revision"],
                    local_dir=folder,
                    token=False,
                    max_workers=2,
                    allow_patterns=[
                        "*.json",
                        "*.jinja",
                        "*.txt",
                        "*.safetensors",
                        "README.md",
                        "LICENSE*",
                    ],
                )
                while not future.done():
                    files = list(folder.glob("*.safetensors"))
                    cache = folder / ".cache/huggingface/download"
                    if cache.exists():
                        files.extend(cache.glob("*.incomplete"))
                    state["downloaded_bytes"] = sum(f.stat().st_size for f in files if f.exists())
                    state["finished_shards"] = len(list(folder.glob("*.safetensors")))
                    save()
                    time.sleep(5)
                future.result()
            shard_names = sorted(set(weights["weight_map"].values()))
            if any(not (folder / name).is_file() for name in shard_names):
                raise RuntimeError("Canonical weight shards are incomplete")
            receipt = {
                **spec,
                "physical_path": str(folder),
                "shards": shard_names,
                "actual_weight_bytes": sum((folder / n).stat().st_size for n in shard_names),
            }
            (folder / "camcat-source.json").write_text(json.dumps(receipt, indent=2) + "\n")
            state["completed_models"].append(role)
            save()
        state.update(state="succeeded", phase="complete")
        save()
        print("Pinned Qwen embedding/reranker downloads completed.", flush=True)
    except Exception as error:
        # Hub exceptions may contain signed download URLs; record only the error class.
        state.update(state="failed", error_type=type(error).__name__)
        save()
        print(
            "Model download failed: " + type(error).__name__ + "; no automatic restart.", flush=True
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
