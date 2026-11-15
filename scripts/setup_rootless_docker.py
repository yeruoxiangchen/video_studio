"""Install official Docker/Compose binaries in project data; never install host packages."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DOCKER_VERSION = "29.9.3"
COMPOSE_VERSION = "v5.7.0"


def download(url: str, target: Path) -> None:
    if target.is_file() and target.stat().st_size:
        return
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "CamCat-Studio"})
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output, 1024 * 1024)
    temporary.replace(target)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    downloads = root / "downloads"
    binaries = root / "tools/docker"
    client = root / "docker/client"
    for directory in (
        downloads,
        binaries,
        client / "cli-plugins",
        root / "docker/data",
        root / "docker/exec",
        root / "docker/rootlesskit",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    receipts = []
    for name in ("docker", "docker-rootless-extras"):
        url = f"https://download.docker.com/linux/static/stable/x86_64/{name}-{DOCKER_VERSION}.tgz"
        target = downloads / f"{name}-{DOCKER_VERSION}.tgz"
        download(url, target)
        unpack = root / "tmp" / f"{name}-{DOCKER_VERSION}"
        unpack.mkdir(parents=True, exist_ok=True)
        with tarfile.open(target, "r:gz") as archive:
            archive.extractall(unpack, filter="data")
        for entry in (unpack / name).iterdir():
            if entry.is_file():
                shutil.copy2(entry, binaries / entry.name)
        receipts.append({"source": url, "sha256": digest(target)})
    release = f"https://github.com/docker/compose/releases/download/{COMPOSE_VERSION}"
    compose_name = "docker-compose-linux-x86_64"
    compose = downloads / f"{compose_name}-{COMPOSE_VERSION}"
    checksums = downloads / f"compose-checksums-{COMPOSE_VERSION}.txt"
    download(release + "/" + compose_name, compose)
    download(release + "/checksums.txt", checksums)
    expected = next(
        line.split()[0]
        for line in checksums.read_text().splitlines()
        if line.split()[-1].lstrip("*") == compose_name
    )
    actual = digest(compose)
    if actual != expected:
        raise RuntimeError("Official Compose checksum differs; refusing execution")
    shutil.copy2(compose, client / "cli-plugins/docker-compose")
    (client / "cli-plugins/docker-compose").chmod(0o755)
    environment = {**os.environ, "DOCKER_CONFIG": str(client)}
    versions = {}
    for name, arguments in (
        ("docker", [str(binaries / "docker"), "--version"]),
        ("compose", [str(binaries / "docker"), "compose", "version"]),
    ):
        versions[name] = subprocess.check_output(arguments, env=environment, text=True).strip()
    missing = [name for name in ("newuidmap", "newgidmap", "slirp4netns") if not shutil.which(name)]
    status = {
        "time": datetime.now(UTC).isoformat(),
        "state": "binaries_ready" if not missing else "awaiting_system_dependencies",
        "missing": missing,
        "versions": versions,
        "archives": receipts,
        "compose_sha256": actual,
        "data_root": str(root / "docker/data"),
    }
    (root / "checks/docker-setup.json").write_text(json.dumps(status, indent=2) + "\n")
    print(json.dumps({"state": status["state"], "versions": versions, "missing": missing}))


if __name__ == "__main__":
    main()
