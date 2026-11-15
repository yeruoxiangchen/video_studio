"""Use the official Hub client and verify the pinned model's LFS/blob identities once."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source))
    from services.local_models.visual_runtime import verify_visual_source

    spec = json.loads((source / "infra/visual-model.lock.json").read_text())
    folder = root / spec["directory"]
    if (folder / "camcat-source.json").is_file():
        verify_visual_source(folder, spec["repository"], spec["revision"])
        print("Pinned visual model receipt and file sizes are valid; no download needed.")
        return
    from huggingface_hub import HfApi, snapshot_download

    information = HfApi().model_info(
        spec["repository"], revision=spec["revision"], files_metadata=True, token=False
    )
    snapshot_download(
        spec["repository"],
        revision=spec["revision"],
        local_dir=folder,
        token=False,
        max_workers=4,
        allow_patterns=["*.json", "*.jinja", "*.safetensors", "*.txt", "LICENSE", "README.md"],
    )
    files = []
    for record in information.siblings or []:
        path = folder / record.rfilename
        if not path.is_file():
            continue
        if Path(record.rfilename).name != record.rfilename:
            raise ValueError("Unexpected nested file in the visual model snapshot")
        size = path.stat().st_size
        if size != record.size:
            raise ValueError("Visual model file is incomplete")
        sha256 = hashlib.sha256()
        blob = hashlib.sha1(f"blob {size}\0".encode())
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                sha256.update(block)
                blob.update(block)
        if record.lfs:
            if sha256.hexdigest() != record.lfs.sha256:
                raise ValueError("Visual model LFS SHA256 mismatch")
        elif blob.hexdigest() != record.blob_id:
            raise ValueError("Visual model Git blob identity mismatch")
        files.append({"file": record.rfilename, "bytes": size, "sha256": sha256.hexdigest()})
    receipt = {
        "repository": spec["repository"],
        "revision": spec["revision"],
        "license": spec["license"],
        "physical_path": str(folder),
        "files": files,
    }
    (folder / "camcat-source.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("Pinned visual model downloaded and verified against the official Hub identities.")


if __name__ == "__main__":
    main()
