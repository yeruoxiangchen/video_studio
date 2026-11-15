"""Install a project-owned CPU ASR runtime and its pinned multilingual weights."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    environment = root / "asr-venv"
    python = environment / "bin/python"
    if not python.is_file():
        subprocess.run([sys.executable, "-B", "-m", "venv", str(environment)], check=True)
    subprocess.run(
        [
            str(python),
            "-B",
            "-m",
            "pip",
            "install",
            "--no-compile",
            "--disable-pip-version-check",
            "--timeout",
            "120",
            "--retries",
            "3",
            "-r",
            str(source / "services/local_models/asr-requirements.txt"),
        ],
        check=True,
    )
    lock = json.loads((source / "infra/local-models.lock.json").read_text())["asr"]
    folder = root / "models" / lock["repository"].rsplit("/", 1)[1]
    download = """import json, sys
from pathlib import Path
from huggingface_hub import snapshot_download
record=json.loads(sys.argv[1]); folder=Path(sys.argv[2])
snapshot_download(record['repository'], revision=record['revision'], local_dir=folder,
                  allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.txt','README.md'],
                  max_workers=4)
(folder/'camcat-source.json').write_text(json.dumps(record,indent=2)+'\\n')
"""
    subprocess.run([str(python), "-B", "-c", download, json.dumps(lock), str(folder)], check=True)
    print("Pinned multilingual ASR weights and isolated CPU runtime are ready.", flush=True)


if __name__ == "__main__":
    main()
