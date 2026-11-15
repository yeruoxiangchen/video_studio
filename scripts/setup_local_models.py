"""Build an isolated model service environment around a read-only Torch 2.8 runtime."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import shutil
import subprocess
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    official = root / "tmp/qwen3-vl-official"
    lock = json.loads((source / "infra/local-models.lock.json").read_text())
    revision = subprocess.check_output(
        ["git", "-C", str(official), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != lock["official_source"]["revision"]:
        raise RuntimeError("Official Qwen source revision differs from the recorded revision")
    vendor = source / "third_party/qwen3_vl"
    receipts = {}
    for original in [official / "LICENSE", *sorted((official / "src").rglob("*.py"))]:
        relative = original.relative_to(official)
        target = vendor / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        checksum = sha(original)
        if target.exists() and sha(target) != checksum:
            raise RuntimeError("Refusing to replace a modified vendored source: " + str(relative))
        shutil.copyfile(original, target)
        receipts[str(relative)] = checksum
    (vendor / "UPSTREAM.json").write_text(
        json.dumps(
            {
                **lock["official_source"],
                "files": receipts,
                "source_modified": False,
            },
            indent=2,
        )
        + "\n"
    )
    environment = root / "local-models-venv"
    if not (environment / "bin/python").is_file():
        subprocess.run(
            ["/home/zjr/anaconda3/envs/chartvlm/bin/python", "-B", "-m", "venv", str(environment)],
            check=True,
        )
    packages = environment / "lib/python3.11/site-packages"
    readonly = Path("/data/zjr/deepsearch_rl/venv/lib/python3.11/site-packages")
    if not (readonly / "torch").is_dir():
        raise RuntimeError("The existing read-only Torch 2.8 runtime is unavailable")
    (packages / "camcat_readonly_torch.pth").write_text(str(readonly) + "\n")
    subprocess.run(
        [
            str(environment / "bin/python"),
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
            str(source / "services/local_models/requirements.txt"),
        ],
        check=True,
    )
    script = """import importlib.metadata as m, json
names=['torch','torchvision','transformers','accelerate','bitsandbytes','qwen-vl-utils','fastapi']
result={n:m.version(n) for n in names}
assert result['torch'].startswith('2.8.'), result['torch']
print(json.dumps(result))
"""
    versions = json.loads(
        subprocess.check_output([str(environment / "bin/python"), "-B", "-c", script], text=True)
    )
    (root / "checks/local-models-environment.json").write_text(
        json.dumps(
            {
                "versions": versions,
                "read_only_runtime": str(readonly),
                "environment": str(environment),
                "official_source": lock["official_source"],
            },
            indent=2,
        )
        + "\n"
    )
    config = root / "config/local-models.env"
    if not config.exists():
        descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(
                "export CAMCAT_LOCAL_MODELS_API_KEY="
                + shlex.quote(secrets.token_urlsafe(32))
                + "\n"
            )
    print("Isolated model environment and unchanged official Qwen source are ready.", flush=True)


if __name__ == "__main__":
    main()
