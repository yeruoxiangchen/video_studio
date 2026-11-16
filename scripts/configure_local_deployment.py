"""Configure the real local providers and preserve unrelated private settings."""

from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path

from dotenv import dotenv_values, set_key


def translation_configuration(existing: dict, core_key: str) -> dict[str, str]:
    fields = ("BASE_URL", "API_KEY", "MODEL", "BACKEND")
    if existing.get("CAMCAT_TRANSLATION_BACKEND") == "hymt":
        result = {
            f"CAMCAT_TRANSLATION_{name}": existing.get(f"CAMCAT_TRANSLATION_{name}")
            for name in fields
        }
        if not all(result.values()):
            raise ValueError("The selected specialist translation configuration is incomplete")
        return {name: str(value) for name, value in result.items()}
    return {
        "CAMCAT_TRANSLATION_BASE_URL": "http://10.0.2.2:18969",
        "CAMCAT_TRANSLATION_API_KEY": core_key,
        "CAMCAT_TRANSLATION_MODEL": "Qwen/Qwen3-VL-8B-Instruct",
        "CAMCAT_TRANSLATION_BACKEND": "chat",
    }


def local_visual_profile(
    existing_model: str | None, requested_model: str | None = None
) -> dict[str, str]:
    ports = {"Qwen/Qwen3-VL-8B-Instruct": 18969, "Qwen/Qwen3.5-9B": 18972}
    if requested_model is not None and requested_model not in ports:
        raise ValueError("Unsupported explicit local visual model")
    selected = requested_model or (
        existing_model if existing_model in ports else "Qwen/Qwen3-VL-8B-Instruct"
    )
    return {
        "CAMCAT_LLM_MODEL": selected,
        "CAMCAT_LLM_BASE_URL": f"http://10.0.2.2:{ports[selected]}",
    }


def local_narrative_profile(
    existing_model: str | None, requested_model: str | None = None
) -> dict[str, str]:
    allowed = {"Qwen/Qwen3.6-27B", "disabled"}
    if requested_model is not None and requested_model not in allowed:
        raise ValueError("Unsupported explicit local narrative model")
    selected = requested_model or (existing_model if existing_model in allowed else "disabled")
    return {
        "CAMCAT_NARRATIVE_BASE_URL": "http://10.0.2.2:18974" if selected != "disabled" else "",
        "CAMCAT_NARRATIVE_MODEL": selected if selected != "disabled" else "",
    }


def local_editing_profile(
    existing_model: str | None, requested_model: str | None = None
) -> dict[str, str]:
    if requested_model not in {None, "inherit", "Qwen/Qwen3.6-27B"}:
        raise ValueError("Unsupported explicit local editing model")
    selected = requested_model or (
        "Qwen/Qwen3.6-27B" if existing_model == "Qwen/Qwen3.6-27B" else "inherit"
    )
    return (
        {"CAMCAT_LLM_MODEL": selected, "CAMCAT_LLM_BASE_URL": "http://10.0.2.2:18974"}
        if selected != "inherit"
        else {}
    )


def with_gateway_profile(values: dict, connection: dict | None) -> dict:
    result = dict(values)
    if connection is None:
        return result
    if result.get("CAMCAT_TRANSLATION_BACKEND", "chat") != "chat":
        raise ValueError("Update gateway channels before changing the translation backend")
    if {provider["role"] for provider in connection["providers"]} != {"LLM", "TRANSLATION"}:
        raise ValueError("The saved gateway channel configuration is incomplete")
    for provider in connection["providers"]:
        role = provider["role"]
        if values.get(f"CAMCAT_{role}_MODEL") != provider["upstream_model"]:
            raise ValueError("Update the gateway channel before changing its upstream model")
        result[f"CAMCAT_{role}_MODEL"] = provider["alias"]
        result[f"CAMCAT_{role}_BASE_URL"] = "http://10.0.2.2:29480"
        result[f"CAMCAT_{role}_API_KEY"] = connection["api_key"]
    result.update(
        CAMCAT_LLM_HEALTH_PATH="/v1/models",
        CAMCAT_LLM_MAX_OUTPUT_TOKENS="4096",
        CAMCAT_GATEWAY_RECEIPTS_DIR="/var/lib/camcat/gateway-receipts",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--visual-model", choices=["Qwen/Qwen3-VL-8B-Instruct", "Qwen/Qwen3.5-9B"])
    parser.add_argument("--narrative-model", choices=["Qwen/Qwen3.6-27B", "disabled"])
    parser.add_argument("--editing-model", choices=["Qwen/Qwen3.6-27B", "inherit"])
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    local = dotenv_values(root / "config/local-models.env")
    speech = dotenv_values(root / "config/speech.env")
    network = dotenv_values(root / "config/network-docker.env")
    if not all(
        [
            local.get("CAMCAT_LOCAL_MODELS_API_KEY"),
            speech.get("CAMCAT_TTS_API_KEY"),
        ]
    ):
        raise RuntimeError("Prepare the private local model configurations first")
    existing = dotenv_values(source / ".env")
    connection_path = root / "config/go-gateway-client.json"
    connection = json.loads(connection_path.read_text()) if connection_path.exists() else None
    if connection:
        # Select and validate the original model profiles before restoring gateway aliases.
        for provider in connection["providers"]:
            existing[f"CAMCAT_{provider['role']}_MODEL"] = provider["upstream_model"]
    visual = local_visual_profile(
        existing.get("CAMCAT_VISUAL_ANALYSIS_MODEL") or existing.get("CAMCAT_LLM_MODEL"),
        args.visual_model,
    )
    editing = local_editing_profile(existing.get("CAMCAT_LLM_MODEL"), args.editing_model)
    narrative = local_narrative_profile(
        existing.get("CAMCAT_NARRATIVE_MODEL"), args.narrative_model
    )
    if narrative["CAMCAT_NARRATIVE_MODEL"] or editing:
        spec = json.loads((source / "infra/planning-model.lock.json").read_text())
        receipt = json.loads((root / spec["directory"] / "camcat-source.json").read_text())
        if any(receipt.get(key) != spec[key] for key in ("repository", "revision")):
            raise RuntimeError("Prepare the pinned native narrative model before selecting it")
    if visual["CAMCAT_LLM_MODEL"] == "Qwen/Qwen3.5-9B":
        spec = json.loads((source / "infra/visual-model.lock.json").read_text())
        receipt = json.loads((root / spec["directory"] / "camcat-source.json").read_text())
        if any(receipt.get(key) != spec[key] for key in ("repository", "revision")):
            raise RuntimeError("Prepare the pinned visual model before selecting it")
    values = {
        "CAMCAT_ENVIRONMENT": "production",
        "CAMCAT_SHARED_RUNTIME": str(root / "runtime"),
        "CAMCAT_OBJECT_STORE_PUBLIC_ENDPOINT": "http://127.0.0.1:18963",
        "CAMCAT_MILVUS_COLLECTION": "camcat_local_2c456551_2048",
        "CAMCAT_LLM_MODEL": "Qwen/Qwen3-VL-8B-Instruct",
        "CAMCAT_LLM_JSON_SCHEMA_ENABLED": "true",
        "CAMCAT_SOURCE_EVIDENCE_ENABLED": "true",
        "CAMCAT_ASR_MODEL": "dropbox-dash/faster-whisper-large-v3-turbo",
        "CAMCAT_TRANSLATION_BASE_URL": "http://10.0.2.2:18969",
        "CAMCAT_TRANSLATION_API_KEY": local["CAMCAT_LOCAL_MODELS_API_KEY"],
        "CAMCAT_TRANSLATION_MODEL": "Qwen/Qwen3-VL-8B-Instruct",
        "CAMCAT_TTS_BASE_URL": "http://speech:18961",
        "CAMCAT_TTS_API_KEY": speech["CAMCAT_TTS_API_KEY"],
        "CAMCAT_TTS_MODEL": "piper",
        "CAMCAT_TTS_VOICE": "en_US-lessac-medium",
        "CAMCAT_PROVIDER_TIMEOUT_SECONDS": "300",
    }
    values.update(translation_configuration(existing, str(local["CAMCAT_LOCAL_MODELS_API_KEY"])))
    for provider in ("EMBEDDING", "RERANKER", "LLM", "ASR"):
        values[f"CAMCAT_{provider}_BASE_URL"] = "http://10.0.2.2:18969"
        values[f"CAMCAT_{provider}_API_KEY"] = local["CAMCAT_LOCAL_MODELS_API_KEY"]
    values.update(visual)
    if editing:
        values.update(editing)
        values["CAMCAT_VISUAL_ANALYSIS_MODEL"] = visual["CAMCAT_LLM_MODEL"]
        values["CAMCAT_VISUAL_ANALYSIS_BASE_URL"] = visual["CAMCAT_LLM_BASE_URL"]
        values["CAMCAT_VISUAL_ANALYSIS_API_KEY"] = local["CAMCAT_LOCAL_MODELS_API_KEY"]
    else:
        values.update(
            CAMCAT_VISUAL_ANALYSIS_MODEL="",
            CAMCAT_VISUAL_ANALYSIS_BASE_URL="",
            CAMCAT_VISUAL_ANALYSIS_API_KEY="",
        )
    values.update(narrative)
    values["CAMCAT_NARRATIVE_API_KEY"] = (
        str(local["CAMCAT_LOCAL_MODELS_API_KEY"]) if narrative["CAMCAT_NARRATIVE_MODEL"] else ""
    )
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        values[f"CAMCAT_CONTAINER_{name}"] = network.get(name) or network.get(name.lower()) or ""
    for name in ("PIXABAY_API_KEY", "CAMCAT_PIXABAY_API_KEY"):
        if existing.get(name) in {"change-me", "change-me-pixabay-key"}:
            values[name] = ""
    values = with_gateway_profile(values, connection)
    for name, value in values.items():
        assert value is not None
        set_key(source / ".env", name, value)
    (source / ".env").chmod(0o600)
    active = root / "config/active-translation.env"
    descriptor = os.open(active, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        for name in ("BASE_URL", "API_KEY", "MODEL", "BACKEND"):
            value = str(values[f"CAMCAT_TRANSLATION_{name}"])
            if name == "BASE_URL":
                from scripts.run_local_integration import local_provider_url

                value = local_provider_url(value)
            stream.write(f"export CAMCAT_TRANSLATION_{name}=" + shlex.quote(value) + "\n")
    (root / "checks/translation-provider.json").write_text(
        json.dumps(
            {
                "mode": "specialist"
                if values["CAMCAT_TRANSLATION_BACKEND"] == "hymt"
                else "shared_core",
                "model": values["CAMCAT_TRANSLATION_MODEL"],
            }
        )
        + "\n"
    )
    print(
        "CamCat configured for real local models, independent Milvus collection and local playback."
    )


if __name__ == "__main__":
    main()
