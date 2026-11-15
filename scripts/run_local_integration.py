"""Run real integration checks from the host against the project's Compose stack."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values
from sqlalchemy.engine import make_url


def local_provider_url(value: str) -> str:
    url = urlsplit(value)
    if url.hostname == "10.0.2.2":
        url = url._replace(netloc=f"127.0.0.1:{url.port}")
    return urlunsplit(url)


def provider_urls(environment: dict[str, str]) -> dict[str, str]:
    result = {}
    for role in (
        "EMBEDDING",
        "RERANKER",
        "LLM",
        "ASR",
        "VISUAL_ANALYSIS",
        "NARRATIVE",
        "TRANSLATION",
    ):
        name = f"CAMCAT_{role}_BASE_URL"
        default = "" if role in {"VISUAL_ANALYSIS", "NARRATIVE"} else "http://10.0.2.2:18969"
        result[name] = local_provider_url(environment.get(name) or default)
    return result


def integration_arguments(root: Path, requested: list[str]) -> list[str]:
    return [
        "-B",
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "-m",
        "integration",
        "--basetemp",
        str(root / "runtime/acceptance/integration"),
        "--junitxml",
        str(root / "runtime/acceptance/integration-results.xml"),
        *(requested or ["tests/integration"]),
    ]


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    private = dotenv_values(source / ".env")
    environment = {
        **os.environ,
        **{key: value for key, value in private.items() if value is not None},
    }
    database = make_url(
        environment.get(
            "CAMCAT_DATABASE_URL", "postgresql+psycopg://camcat:camcat@postgres:5432/camcat"
        )
    ).set(host="127.0.0.1", port=int(environment.get("CAMCAT_PG_PORT", "18970")))
    environment.update(
        CAMCAT_DATABASE_URL=database.render_as_string(hide_password=False),
        CAMCAT_ENVIRONMENT="test",
        CAMCAT_RUNTIME_DIR=str(root / "runtime"),
        CAMCAT_OBJECT_STORE_ENDPOINT="http://127.0.0.1:18963",
        CAMCAT_OBJECT_STORE_PUBLIC_ENDPOINT="http://127.0.0.1:18963",
        CAMCAT_MILVUS_URI="http://127.0.0.1:18965",
        CAMCAT_TTS_BASE_URL="http://127.0.0.1:18961",
        CAMCAT_TEST_API_URL="http://127.0.0.1:18960",
        CAMCAT_EXTERNAL_TEST_VIDEO=str(root / "runtime/acceptance/source.mp4"),
        CAMCAT_LOCALIZATION_EXTERNAL="1",
        CAMCAT_LOCAL_MODELS_EXTERNAL="1",
        CAMCAT_TEST_SOURCE_TIMEOUT="900",
        CAMCAT_TEST_AGENT_TIMEOUT="900",
        PYTHONPATH=str(source / "apps/api") + os.pathsep + str(source),
    )
    environment.update(provider_urls(environment))
    arguments = [
        sys.executable,
        *integration_arguments(root, sys.argv[1:]),
    ]
    os.execve(sys.executable, arguments, environment)


if __name__ == "__main__":
    main()
