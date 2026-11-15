"""Compare host/owned Docker-network connectivity without printing proxy credentials."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

PROBE = """
import concurrent.futures, json, socket, urllib.request, urllib.error, urllib.parse, os
def check(url):
    host = urllib.parse.urlsplit(url).hostname
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return {'host': host, 'http': response.status}
    except urllib.error.HTTPError as error:
        return {'host': host, 'http': error.code}
    except Exception as error:
        return {'host': host, 'error_type': type(error).__name__}
p = urllib.parse.urlsplit(os.environ.get('HTTPS_PROXY', ''))
if p.hostname:
    try:
        with socket.create_connection((p.hostname, p.port or 80), timeout=5):
            print(json.dumps({'proxy_tcp': True}))
    except Exception as error:
        print(json.dumps({'proxy_tcp': False, 'error_type': type(error).__name__}))
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    for value in pool.map(check, ['https://quay.io/v2/', 'https://public.ecr.aws/v2/']):
        print(json.dumps(value))
"""


def load(path: Path) -> dict[str, str]:
    return {
        name: json.loads(value)
        for name, value in (line.split("=", 1) for line in path.read_text().splitlines())
    }


def main() -> None:
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    python = str(root / "venv/bin/python")
    pid = (root / "docker/rootlesskit/child_pid").read_text().strip()
    for scope, file, prefix in (
        ("host", "network.env", []),
        (
            "docker_namespace",
            "network-docker.env",
            ["nsenter", "-U", "--preserve-credentials", "-m", "-n", "-t", pid],
        ),
    ):
        result = subprocess.run(
            [*prefix, python, "-B", "-c", PROBE],
            env={**os.environ, **load(root / "config" / file)},
            capture_output=True,
            text=True,
            timeout=45,
        )
        print(scope, result.returncode, result.stdout.strip(), flush=True)
        if result.returncode:
            print("namespace command error; see exit code", flush=True)


if __name__ == "__main__":
    main()
