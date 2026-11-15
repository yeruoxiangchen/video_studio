"""Record only proxy settings in private systemd EnvironmentFiles."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def write_private(path: Path, values: dict[str, str]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        for name, value in values.items():
            stream.write(name + "=" + json.dumps(value, ensure_ascii=False) + "\n")
    path.chmod(0o600)


def proxy_environments(environment: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    names = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    )
    values = {name: environment[name] for name in names if environment.get(name)}
    for name in names[:3]:
        value = environment.get(name) or environment.get(name.lower())
        if value:
            values[name] = values[name.lower()] = value
    local = (
        "localhost,127.0.0.1,::1,10.0.2.2,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,"
        "api,postgres,minio,milvus,etcd,provider-gateway,speech"
    )
    values.update(NO_PROXY=local, no_proxy=local)
    docker = dict(values)
    for name in names[:6]:
        if name not in docker:
            continue
        parts = urlsplit(docker[name])
        if parts.hostname in {"127.0.0.1", "localhost", "::1"}:
            auth = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
            netloc = auth + "10.0.2.2" + (":" + str(parts.port) if parts.port else "")
            docker[name] = urlunsplit(
                (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
            )
    return values, docker


def main() -> None:
    folder = Path(os.environ["CAMCAT_STUDIO_DATA"]) / "config"
    folder.mkdir(parents=True, exist_ok=True)
    values, docker = proxy_environments(os.environ)
    write_private(folder / "network.env", values)
    write_private(folder / "network-docker.env", docker)
    print("Private network settings saved for owned services; no values printed.")


if __name__ == "__main__":
    main()
