"""Prepare pinned upstream CPU perception models in project-owned data storage."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import venv
import zipfile
from pathlib import Path
from urllib.request import urlopen


def main() -> None:
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    source = Path(__file__).resolve().parents[1]
    spec = json.loads((source / "infra/geometry.lock.json").read_text())
    target = root / Path(spec["python"]).parent.parent
    if not (target / "bin/python").is_file():
        venv.EnvBuilder(with_pip=True).create(target)
    subprocess.run(
        [
            str(target / "bin/python"),
            "-B",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(source / "services/local_models/geometry-requirements.txt"),
        ],
        check=True,
    )
    directory = root / spec["directory"]
    directory.mkdir(parents=True, exist_ok=True)
    for record in spec["models"].values():
        path = directory / record["file"]
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]:
            continue
        with urlopen(record["url"], timeout=120) as response:
            archive = response.read(64 * 1024 * 1024)
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            candidates = [name for name in package.namelist() if Path(name).suffix == ".onnx"]
            if len(candidates) != 1:
                raise ValueError("Expected one upstream ONNX model per pinned archive")
            contents = package.read(candidates[0])
        if (
            len(contents) != record["bytes"]
            or hashlib.sha256(contents).hexdigest() != record["sha256"]
        ):
            raise ValueError("Upstream CPU model differs from its pinned SHA256")
        temporary = path.with_suffix(".part")
        temporary.write_bytes(contents)
        temporary.replace(path)
    sys.path.insert(0, str(source))
    from services.local_models.geometry_tool import verify_models

    verify_models(root, spec)
    (directory / "camcat-source.json").write_text(json.dumps(spec, indent=2) + "\n")
    print("Pinned RTMLib CPU runtime and upstream model SHA256 identities verified.")


if __name__ == "__main__":
    main()
