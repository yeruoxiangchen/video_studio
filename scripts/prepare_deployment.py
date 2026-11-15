"""Create only project-owned deployment directories and ignored local secrets."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    data = Path(os.environ["CAMCAT_STUDIO_DATA"])
    for name in ("postgres", "etcd", "minio", "milvus"):
        (data / "containers" / name).mkdir(parents=True, exist_ok=True)
    (data / "runtime").mkdir(parents=True, exist_ok=True)
    for name in (
        "tmp",
        "cache/torch",
        "cache/huggingface/hub",
        "cache/torch_extensions",
        "cache/matplotlib",
        "cache/xdg",
    ):
        (data / "runtime" / name).mkdir(parents=True, exist_ok=True)
    environment = source / ".env"
    text = (
        environment.read_text() if environment.exists() else (source / ".env.example").read_text()
    )
    speech_env = data / "config" / "speech.env"
    if not speech_env.exists():
        raise SystemExit(
            "Run prepare-speech first to prepare the private local voice configuration"
        )
    speech = dict(
        line.removeprefix("export ").split("=", 1)
        for line in speech_env.read_text().splitlines()
        if line.startswith("export ")
    )
    text = text.replace(
        "CAMCAT_TTS_API_KEY=\n", f"CAMCAT_TTS_API_KEY={speech['CAMCAT_TTS_API_KEY']}\n"
    )
    text = text.replace(
        "CAMCAT_OBJECT_STORE_SECRET_KEY=change-me-minio-secret",
        "CAMCAT_OBJECT_STORE_SECRET_KEY=" + secrets.token_urlsafe(32),
    )
    descriptor = os.open(environment, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
    environment.chmod(0o600)
    print(
        "Project volume directories and private .env ready. "
        "Core model services still need configuration."
    )


if __name__ == "__main__":
    main()
