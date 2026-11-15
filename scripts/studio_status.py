"""Compact live operator view; model readiness is separate from application readiness."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

CORE_SERVICES = {
    "api",
    "worker",
    "render-worker",
    "localization-worker",
    "web",
    "postgres",
    "minio",
    "etcd",
    "milvus",
    "speech",
}


def required_generation_roles(configuration: dict[str, str | None]) -> dict[str, bool]:
    return {
        "visual_required": (
            configuration.get("CAMCAT_VISUAL_ANALYSIS_MODEL")
            or configuration.get("CAMCAT_LLM_MODEL")
        )
        == "Qwen/Qwen3.5-9B",
        "narrative_required": bool(configuration.get("CAMCAT_NARRATIVE_BASE_URL"))
        or configuration.get("CAMCAT_LLM_MODEL") == "Qwen/Qwen3.6-27B",
    }


def render_status(snapshot: dict[str, Any]) -> str:
    containers = snapshot.get("containers", [])
    running = {
        item["Service"]
        for item in containers
        if item.get("State") == "running" and item.get("Health") in {None, "", "healthy"}
    }
    ready = bool(snapshot.get("api_ready")) and running >= CORE_SERVICES
    local = snapshot.get("local_models", {})
    if snapshot.get("core_required"):
        ready = ready and bool(snapshot.get("core_api_ready")) and local.get("state") == "ready"
    visual = snapshot.get("visual_model", {})
    if snapshot.get("visual_required"):
        ready = ready and bool(snapshot.get("visual_api_ready")) and visual.get("state") == "ready"
    narrative = snapshot.get("narrative_model", {})
    if snapshot.get("narrative_required"):
        ready = (
            ready
            and bool(snapshot.get("narrative_api_ready"))
            and narrative.get("state") == "ready"
        )
    if snapshot.get("specialist_translation_required"):
        ready = (
            ready
            and bool(snapshot.get("specialist_translation_api_ready"))
            and snapshot.get("translation", {}).get("state") == "ready"
        )
    lines = [
        "剪辑工作台",
        "整站：" + ("就绪" if ready else "准备中"),
        "Docker：" + ("就绪" if snapshot.get("docker_ready") else "尚未就绪"),
    ]
    if containers:
        lines.append(
            "容器："
            + " | ".join(
                f"{item['Service']}={item.get('Health') or item.get('State')}"
                for item in containers
            )
        )
    else:
        lines.append("容器：尚未启动")
    download = snapshot.get("download", {})
    if download:
        if download.get("state") == "succeeded":
            lines.append("核心权重：下载完成")
        else:
            written = download.get("downloaded_bytes", 0) / 2**30
            expected = download.get("expected_weight_bytes", 0) / 2**30
            lines.append(
                f"核心权重：{download.get('phase')} | {download.get('state')} | "
                f"{written:.2f}/{expected:.2f} GiB"
            )
    core_state = local.get("state")
    core_display = local.get("phase") if core_state in {"starting", "loading"} else core_state
    lines.append("核心模型：" + str(core_display or local.get("phase") or "尚未启动"))
    if snapshot.get("visual_required"):
        lines.append(
            f"视觉规划：{visual.get('state', '尚未启动')} | "
            f"{visual.get('visual', 'Qwen/Qwen3.5-9B')} | GPU {visual.get('gpu_index', '-')}"
        )
    translation = snapshot.get("translation", {})
    if snapshot.get("narrative_required"):
        lines.append(
            f"叙事规划：{narrative.get('state', '尚未启动')} | "
            f"{narrative.get('model', '-')} | GPU {narrative.get('gpu_index', '-')}"
        )
    if snapshot.get("shared_translation"):
        gpu = next(
            (item.get("index") for item in local.get("gpus", []) if item.get("role") == "visual"),
            "-",
        )
        translation = {"state": local.get("state", "starting"), "gpu_index": gpu}
    lines.append(
        "翻译模型："
        + str(translation.get("state", "未知"))
        + f" | GPU {translation.get('gpu_index', '-')}"
    )
    lines.append("配音模型：" + str(snapshot.get("speech", "未知")))
    bootstrap = snapshot.get("bootstrap", {})
    if bootstrap:
        lines.append(f"部署任务：{bootstrap.get('phase')} | {bootstrap.get('state')}")
        if bootstrap.get("error_type"):
            error = str(bootstrap["error_type"])
            if bootstrap.get("http_status"):
                error += f" | HTTP {bootstrap['http_status']}"
            if bootstrap.get("error_reason"):
                error += " | " + str(bootstrap["error_reason"])
            lines.append("部署错误：" + error)
    if ready:
        lines.append("网页：http://127.0.0.1:18962")
    lines.append("日志：/data/zjr/camcat_studio/logs/")
    return "\n".join(lines)


def main() -> None:
    from dotenv import dotenv_values

    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    source = Path(__file__).resolve().parents[1]
    configuration = dotenv_values(source / ".env")
    specialist = configuration.get("CAMCAT_TRANSLATION_BACKEND") == "hymt"

    def read(name: str) -> dict[str, Any]:
        try:
            return json.loads((root / "checks" / name).read_text())
        except (FileNotFoundError, ValueError):
            return {}

    snapshot = {
        "translation": read(
            "specialist-translation-server.json" if specialist else "translation-server.json"
        ),
        "download": read("model-download.json"),
        "local_models": read("local-models-server.json"),
        "shared_translation": not specialist
        and read("translation-provider.json").get("mode") == "shared_core",
        "specialist_translation_required": specialist,
        "specialist_translation_api_ready": False,
        "bootstrap": read("bootstrap.json"),
        "containers": [],
        "docker_ready": False,
        "api_ready": False,
        "core_required": ":18969" in str(configuration.get("CAMCAT_EMBEDDING_BASE_URL", "")),
        "core_api_ready": False,
        **required_generation_roles(configuration),
        "visual_model": read("visual-model-server.json"),
        "visual_api_ready": False,
        "narrative_model": read("planning-model-server.json"),
        "narrative_api_ready": False,
    }
    speech = subprocess.run(
        ["systemctl", "--user", "show", "camcat-studio-speech", "-p", "ActiveState", "--value"],
        capture_output=True,
        text=True,
    )
    snapshot["speech"] = speech.stdout.strip() or "未知"
    if shutil.which("docker"):
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        snapshot["docker_ready"] = result.returncode == 0
        if snapshot["docker_ready"]:
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--project-directory",
                    str(source),
                    "--env-file",
                    str(source / ".env"),
                    "ps",
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                payload = result.stdout.strip()
                snapshot["containers"] = (
                    json.loads(payload)
                    if payload.startswith("[")
                    else [json.loads(line) for line in payload.splitlines()]
                )
    if any(item.get("Service") == "api" for item in snapshot["containers"]):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:18960/health/ready", timeout=3) as response:
                snapshot["api_ready"] = response.status == 200
        except (OSError, TimeoutError):
            pass
    if snapshot["core_required"]:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:18969/health", timeout=3) as response:
                payload = json.loads(response.read())
                snapshot["core_api_ready"] = (
                    response.status == 200
                    and payload.get("embedding") == "Qwen/Qwen3-VL-Embedding-8B"
                    and payload.get("dimensions") == 2048
                )
        except (OSError, TimeoutError, ValueError):
            pass
    if snapshot["visual_required"]:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:18972/health", timeout=3) as response:
                payload = json.loads(response.read())
                snapshot["visual_api_ready"] = (
                    response.status == 200
                    and payload.get("visual") == "Qwen/Qwen3.5-9B"
                    and payload.get("state") == "ready"
                )
        except (OSError, TimeoutError, ValueError):
            pass
    if snapshot["narrative_required"]:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:18974/health", timeout=3) as response:
                payload = json.loads(response.read())
                snapshot["narrative_api_ready"] = (
                    response.status == 200 and payload.get("status") == "ok"
                )
        except (OSError, TimeoutError, ValueError):
            pass
    if specialist:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            port = snapshot["translation"].get("port", 18976)
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                snapshot["specialist_translation_api_ready"] = (
                    response.status == 200 and json.loads(response.read()).get("status") == "ok"
                )
        except (OSError, TimeoutError, ValueError):
            pass
    print(render_status(snapshot))


if __name__ == "__main__":
    main()
