"""Install scoring packages in a project-owned overlay without changing model runtimes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from pathlib import Path


def main() -> None:
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    source = Path(__file__).resolve().parents[1]
    folder = root / "localization-eval-venv"
    if not (folder / "bin/python").exists():
        venv.EnvBuilder(with_pip=True).create(folder)
    executable = folder / "bin/python"
    own_site = Path(
        subprocess.check_output(
            [str(executable), "-B", "-c", "import site;print(site.getsitepackages()[0])"], text=True
        ).strip()
    )
    runtime_site = (
        root / f"venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    if not runtime_site.is_dir():
        raise RuntimeError("The existing project runtime is required for the evaluation overlay")
    (own_site / "camcat-runtime.pth").write_text(str(runtime_site) + "\n")
    subprocess.run(
        [
            str(executable),
            "-B",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--timeout",
            "120",
            "--retries",
            "3",
            "-r",
            str(source / "requirements.evaluation.txt"),
        ],
        check=True,
    )
    result = subprocess.check_output(
        [
            str(executable),
            "-B",
            "-c",
            "import json,importlib.metadata as m;"
            "print(json.dumps({k:m.version(k) for k in ['sacrebleu','jiwer','pysubs2']}))",
        ],
        text=True,
    )
    (root / "checks/evaluation-environment.json").write_text(
        json.dumps({"python": str(executable), "metrics": json.loads(result)}, indent=2) + "\n"
    )
    print("Project evaluation overlay ready; existing model environments preserved.")


if __name__ == "__main__":
    main()
