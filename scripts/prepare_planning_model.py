"""Prepare pinned GGUF weights and build the unchanged official llama.cpp source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from urllib.request import urlopen

ARCHIVE_SHA256 = "16807cb72e989e2191b2e6d99aa8d3d859ab870ce82fb352b52663fc10e287c6"


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--cuda-compiler", default=shutil.which("nvcc"))
    args = parser.parse_args()
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    source = Path(__file__).resolve().parents[1]
    spec = json.loads((source / "infra/planning-model.lock.json").read_text())
    folder = root / spec["directory"]
    folder.mkdir(parents=True, exist_ok=True)
    receipt_path = folder / "camcat-source.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
    previous = {row["file"]: row for row in receipt.get("files", [])}
    files = []
    for expected in spec["files"]:
        path = folder / expected["file"]
        old = previous.get(expected["file"], {})
        cached = (
            receipt.get("repository") == spec["repository"]
            and receipt.get("revision") == spec["revision"]
            and all(old.get(k) == expected[k] for k in ("bytes", "sha256"))
            and path.is_file()
            and path.stat().st_size == expected["bytes"]
            and path.stat().st_mtime_ns == old.get("mtime_ns")
        )
        if not cached:
            if not path.is_file() or path.stat().st_size != expected["bytes"]:
                if args.offline:
                    raise ValueError("Pinned planning weights are incomplete in offline mode")
                from huggingface_hub import hf_hub_download

                hf_hub_download(
                    spec["repository"],
                    expected["file"],
                    revision=spec["revision"],
                    local_dir=folder,
                    token=False,
                )
            if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
                raise ValueError("Planning model SHA256 differs from its pinned identity")
        files.append({**expected, "mtime_ns": path.stat().st_mtime_ns})
    updated = {
        "repository": spec["repository"],
        "revision": spec["revision"],
        "license": spec["license"],
        "base_model": spec["base_model"],
        "base_revision": spec["base_revision"],
        "files": files,
    }
    if any(receipt.get(k) != v for k, v in updated.items()):
        receipt_path.write_text(json.dumps(updated, indent=2) + "\n")
    runtime = root / spec["runtime_directory"]
    build_path = runtime / "camcat-build.json"
    build = json.loads(build_path.read_text()) if build_path.is_file() else {}
    if (
        build.get("revision") == spec["runtime_revision"]
        and build.get("archive_sha256") == ARCHIVE_SHA256
        and build.get("source_modified") is False
        and (runtime / "bin/llama-server").is_file()
    ):
        print("Pinned weights and official-source runtime receipts verified; no download or build.")
        return
    if args.offline:
        raise ValueError("The pinned native runtime build is unavailable in offline mode")
    if not args.cuda_compiler:
        raise ValueError("Pass --cuda-compiler with the installed nvcc path")
    archive = root / "cache" / f"llama-source-{spec['runtime_revision']}.tar.gz"
    if not archive.is_file():
        archive.parent.mkdir(parents=True, exist_ok=True)
        with (
            urlopen(
                f"https://codeload.github.com/ggml-org/llama.cpp/tar.gz/{spec['runtime_revision']}",
                timeout=120,
            ) as response,
            archive.with_suffix(".part").open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
        archive.with_suffix(".part").replace(archive)
    if digest(archive) != ARCHIVE_SHA256:
        raise ValueError("Official llama.cpp source archive SHA256 mismatch")
    work = root / "tmp" / f"llama.cpp-{spec['runtime_revision']}"
    # Reconstruct this owned temporary source tree from the verified archive.
    # A previous local edit must never enter a build labelled unchanged upstream.
    if work.is_dir():
        shutil.rmtree(work)
    with tarfile.open(archive) as package:
        package.extractall(root / "tmp", filter="data")
    commands = [
        [
            "cmake",
            "-S",
            str(work),
            "-B",
            str(runtime),
            "-DGGML_CUDA=ON",
            f"-DCMAKE_CUDA_COMPILER={args.cuda_compiler}",
            "-DCMAKE_CUDA_ARCHITECTURES=86",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DLLAMA_CURL=OFF",
            "-DLLAMA_BUILD_TESTS=OFF",
            "-DGGML_CUDA_FA_ALL_QUANTS=OFF",
        ],
        ["cmake", "--build", str(runtime), "--target", "llama-server", "--parallel", "6"],
    ]
    for command in commands:
        subprocess.run(command, check=True)
    build_path.write_text(
        json.dumps(
            {
                "repository": spec["runtime_repository"],
                "revision": spec["runtime_revision"],
                "source_modified": False,
                "archive_sha256": ARCHIVE_SHA256,
                "commands": commands,
                "binary": str(runtime / "bin/llama-server"),
            },
            indent=2,
        )
        + "\n"
    )
    print("Pinned weights verified and unchanged official CUDA runtime built.")


if __name__ == "__main__":
    main()
