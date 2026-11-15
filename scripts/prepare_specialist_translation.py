"""Fetch pinned official weights with curl and reuse the existing verified native build."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from scripts.prepare_planning_model import digest
from scripts.serve_specialist_translation import validate_runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    source = Path(__file__).resolve().parents[1]
    lock = json.loads((source / "infra/specialist-translation.lock.json").read_text())
    folder = root / lock["directory"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / lock["file"]
    receipt = folder / "camcat-source.json"
    old = json.loads(receipt.read_text()) if receipt.is_file() else {}
    fields = ("repository", "revision", "file", "bytes", "sha256")
    cached = (
        path.is_file()
        and path.stat().st_size == lock["bytes"]
        and path.stat().st_mtime_ns == old.get("mtime_ns")
        and all(old.get(k) == lock[k] for k in fields)
    )
    if not cached:
        if not path.is_file() or path.stat().st_size != lock["bytes"]:
            if args.offline:
                raise ValueError("The pinned specialist translation weights are incomplete")
            partial = path.with_suffix(".gguf.part")
            url = (
                "https://modelscope.cn/models/Tencent-Hunyuan/Hy-MT2-1.8B-GGUF/resolve/master/"
                + lock["file"]
            )
            subprocess.run(
                [
                    "curl",
                    "--noproxy",
                    "*",
                    "--fail",
                    "--location",
                    "--connect-timeout",
                    "10",
                    "--max-time",
                    "900",
                    "--retry",
                    "2",
                    "--continue-at",
                    "-",
                    "--output",
                    str(partial),
                    url,
                ],
                check=True,
            )
            if partial.stat().st_size != lock["bytes"] or digest(partial) != lock["sha256"]:
                raise ValueError("Official translation weight SHA256 mismatch")
            partial.replace(path)
        elif digest(path) != lock["sha256"]:
            raise ValueError("Official translation weight SHA256 mismatch")
        receipt.write_text(
            json.dumps(
                {
                    **{k: lock[k] for k in fields},
                    "mtime_ns": path.stat().st_mtime_ns,
                    "transport": (
                        "Official ModelScope mirror; bytes verified against pinned HF LFS SHA256"
                    ),
                },
                indent=2,
            )
            + "\n"
        )
    runtime = root / lock["runtime_directory"]
    validate_runtime(
        json.loads((runtime / "camcat-build.json").read_text()), lock["runtime_revision"]
    )
    if not (runtime / "bin/llama-server").is_file():
        raise ValueError("Prepare the pinned native runtime with prepare-planning-model first")
    print("Pinned specialist weights and unmodified native runtime verified.")


if __name__ == "__main__":
    main()
