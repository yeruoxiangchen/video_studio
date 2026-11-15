from __future__ import annotations

from scripts.studio_status import render_status


def test_shared_translation_follows_core_model_health_and_gpu() -> None:
    text = render_status(
        {
            "shared_translation": True,
            "translation": {"state": "ready", "gpu_index": 2},
            "local_models": {"state": "loading", "gpus": [{"role": "visual", "index": 5}]},
        }
    )
    assert "翻译模型：loading | GPU 5" in text
    assert "翻译模型：ready" not in text


def test_model_ready_does_not_label_an_unstarted_application_ready() -> None:
    text = render_status(
        {
            "docker_ready": True,
            "api_ready": False,
            "containers": [],
            "translation": {"state": "ready", "gpu_index": 2},
        }
    )
    assert "整站：准备中" in text
    assert "翻译模型：ready" in text
    assert "整站：就绪" not in text


def test_application_ready_requires_actual_api_health_and_running_core_services() -> None:
    names = (
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
    )
    containers = [{"Service": name, "State": "running", "Health": "healthy"} for name in names]
    assert "整站：就绪" in render_status(
        {"docker_ready": True, "api_ready": True, "containers": containers}
    )
    containers[1]["State"] = "exited"
    assert "整站：准备中" in render_status(
        {"docker_ready": True, "api_ready": True, "containers": containers}
    )


def test_application_readiness_requires_the_render_lane() -> None:
    names = ("api", "worker", "web", "postgres", "minio", "etcd", "milvus", "speech")
    containers = [{"Service": name, "State": "running", "Health": "healthy"} for name in names]
    assert "整站：准备中" in render_status(
        {"docker_ready": True, "api_ready": True, "containers": containers}
    )


def test_application_readiness_requires_the_localization_lane() -> None:
    from scripts.studio_status import CORE_SERVICES

    containers = [
        {"Service": name, "State": "running", "Health": "healthy"}
        for name in CORE_SERVICES - {"localization-worker"}
    ]
    assert "整站：准备中" in render_status(
        {"docker_ready": True, "api_ready": True, "containers": containers}
    )


def test_failed_deployment_exposes_http_status_and_actionable_api_message() -> None:
    text = render_status(
        {
            "bootstrap": {
                "phase": "import_licensed_video",
                "state": "failed",
                "error_type": "HTTPError",
                "http_status": 422,
                "error_reason": "远程地址返回的媒体类型不受支持",
            }
        }
    )
    assert "HTTP 422" in text
    assert "远程地址返回的媒体类型不受支持" in text


def test_specialist_translation_readiness_is_required_for_the_application():
    from scripts.studio_status import CORE_SERVICES

    snapshot = {
        "docker_ready": True,
        "api_ready": True,
        "containers": [
            {"Service": name, "State": "running", "Health": "healthy"} for name in CORE_SERVICES
        ],
        "specialist_translation_required": True,
        "specialist_translation_api_ready": False,
        "translation": {"state": "failed", "gpu_index": 1},
    }
    assert "整站：准备中" in render_status(snapshot)
    snapshot["specialist_translation_api_ready"] = True
    snapshot["translation"]["state"] = "ready"
    assert "整站：就绪" in render_status(snapshot)
