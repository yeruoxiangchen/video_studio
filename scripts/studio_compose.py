"""Invoke the real Compose CLI with literal private proxy values; never evaluate them."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    environment = os.environ.copy()
    network = root / "config/network-docker.env"
    if "build" in sys.argv[1:] and network.is_file():
        for line in network.read_text().splitlines():
            name, value = line.split("=", 1)
            if name in {
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
            }:
                environment[name] = json.loads(value)
    os.execvpe(
        "docker",
        [
            "docker",
            "compose",
            "--project-directory",
            str(source),
            "--env-file",
            str(source / ".env"),
            *sys.argv[1:],
        ],
        environment,
    )


if __name__ == "__main__":
    main()
