"""Reuse an explicitly supplied local OpenAI-compatible service without exposing its key."""

from __future__ import annotations

import argparse
import os
import shlex
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-env", type=Path, required=True)
    parser.add_argument("--model")
    args = parser.parse_args()
    values = dotenv_values(args.from_env)
    base = str(values.get("OPENAI_BASE_URL") or values.get("OPENAI_API_BASE") or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    key = values.get("OPENAI_API_KEY")
    model = args.model or values.get("OPENAI_MODEL") or values.get("OPENAI_MODEL_NAME")
    if urlparse(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            "This import command accepts an explicitly configured loopback service only"
        )
    if not key or not model:
        raise SystemExit("Source configuration needs OPENAI_API_KEY and a model name")
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    target = root / "config" / "translation.env"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        for name, value in {"BASE_URL": base, "API_KEY": key, "MODEL": str(model)}.items():
            stream.write(f"export CAMCAT_TRANSLATION_{name}={shlex.quote(value)}\n")
    print("Private local translation configuration imported; the shared service was not modified.")


if __name__ == "__main__":
    main()
