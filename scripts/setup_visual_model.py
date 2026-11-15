"""Create an owned inference overlay without modifying the canonical model environment."""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path


def main() -> None:
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Use the project's Python 3.11 local-models environment")
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    source = Path(__file__).resolve().parents[1]
    target = root / "quality-models-venv"
    if not (target / "bin/python").is_file():
        venv.EnvBuilder(with_pip=True).create(target)
    sites = list(dict.fromkeys(path for path in sys.path if path.endswith("site-packages")))
    (target / "lib/python3.11/site-packages/camcat_readonly_runtime.pth").write_text(
        "\n".join(sites) + "\n"
    )
    subprocess.run(
        [
            str(target / "bin/python"),
            "-B",
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "--find-links",
            str(root / "cache/wheels"),
            "--disable-pip-version-check",
            "-r",
            str(source / "services/local_models/visual-requirements.txt"),
        ],
        check=True,
    )
    print("Visual runtime overlay prepared; the canonical runtime was read only.")


if __name__ == "__main__":
    main()
