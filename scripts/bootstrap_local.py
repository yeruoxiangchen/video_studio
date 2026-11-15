"""One owned deployment run. Real commands only; stop at failures without auto-restarting."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    status = root / "checks/bootstrap.json"
    state = {"state": "running", "phase": "start", "pid": os.getpid(), "automatic_restart": False}

    def save(phase: str) -> None:
        state.update(phase=phase, time=datetime.now(UTC).isoformat())
        temporary = status.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        temporary.replace(status)
        print(json.dumps({"phase": phase, "state": state["state"]}), flush=True)

    def command(
        args: list[str],
        phase: str,
        filename: str,
        *,
        timeout: int = 7200,
        environment: dict[str, str] | None = None,
    ) -> None:
        state["log"] = str(root / "logs" / filename)
        save(phase)
        with (root / "logs" / filename).open("a") as log:
            subprocess.run(
                args,
                cwd=source,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment or os.environ.copy(),
                timeout=timeout,
                check=True,
            )

    studio = ["bash", "scripts/studio.sh"]
    compose = [*studio, "compose", "--profile", "local-speech", "--profile", "test"]
    try:
        command(
            [*studio, "prepare-local-models"], "prepare_model_environment", "local-models-setup.log"
        )
        command([*studio, "prepare-asr"], "prepare_multilingual_asr", "asr-setup.log")
        command([*studio, "configure-local"], "configure_local_services", "configure-local.log")
        network = dotenv_values(root / "config/network-docker.env")
        build_env = {
            **os.environ,
            **{name: value for name, value in network.items() if value is not None},
        }
        command(
            [
                *compose,
                "build",
                "--build-arg",
                "HTTP_PROXY",
                "--build-arg",
                "HTTPS_PROXY",
                "--build-arg",
                "NO_PROXY",
                "--build-arg",
                "http_proxy",
                "--build-arg",
                "https_proxy",
                "--build-arg",
                "no_proxy",
                "minio",
                "api",
                "worker",
                "render-worker",
                "localization-worker",
                "web",
                "speech",
                "migrate",
            ],
            "build_application_images",
            "stack-build.log",
            environment=build_env,
        )
        command(
            [*compose, "up", "-d", "--wait", "postgres", "etcd", "minio", "milvus"],
            "start_infrastructure",
            "infra-start.log",
        )
        save("wait_model_download")
        deadline = time.monotonic() + 12 * 3600
        while time.monotonic() < deadline:
            download = json.loads((root / "checks/model-download.json").read_text())
            if download["state"] == "succeeded":
                break
            if download["state"] == "failed":
                raise RuntimeError("model_download_failed")
            time.sleep(10)
        else:
            raise TimeoutError("model_download_deadline")
        active = subprocess.run(
            ["systemctl", "--user", "is-active", "camcat-studio-local-models"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if active != "active":
            command(
                [*studio, "start-local-models"],
                "start_core_models",
                "local-models-start.log",
                timeout=60,
            )
        save("wait_core_model_ready")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            try:
                with opener.open("http://127.0.0.1:18969/health", timeout=5) as response:
                    if response.status == 200:
                        break
            except (OSError, TimeoutError):
                pass
            check = subprocess.run(
                ["systemctl", "--user", "is-failed", "camcat-studio-local-models"],
                capture_output=True,
                text=True,
            )
            if check.stdout.strip() == "failed":
                raise RuntimeError("core_models_failed")
            time.sleep(5)
        else:
            raise TimeoutError("model_startup_deadline")
        command(
            [
                *compose,
                "up",
                "-d",
                "--wait",
                "api",
                "worker",
                "localization-worker",
                "render-worker",
                "web",
                "speech",
            ],
            "start_application",
            "stack-start.log",
        )
        save("wait_api_ready")
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                with opener.open("http://127.0.0.1:18960/health/ready", timeout=10) as response:
                    if response.status == 200:
                        break
            except (OSError, TimeoutError):
                pass
            time.sleep(3)
        else:
            raise TimeoutError("api_readiness_deadline")
        command(
            [*studio, "prepare-acceptance-media"],
            "prepare_real_media",
            "acceptance-media.log",
            timeout=600,
        )
        command(
            [
                *compose,
                "exec",
                "-T",
                "api",
                "python",
                "scripts/seed_open_library.py",
                "--count",
                "0",
                "--max-duration",
                "20",
            ],
            "seed_licensed_library",
            "library-seed.log",
            timeout=1200,
        )
        command(
            [*studio, "integration-local"],
            "real_integration_and_migrations",
            "integration-real.log",
            timeout=7200,
        )
        state["log"] = str(root / "logs/library-import.log")
        save("import_licensed_video")
        jobs = []
        for item in json.loads((source / "docs/open-video-library.json").read_text()):
            payload = {
                key: item[key] for key in ("download_url", "source_url", "license_name", "filename")
            }
            request = urllib.request.Request(
                "http://127.0.0.1:18960/api/v1/videos/import",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(request, timeout=30) as response:
                imported = json.load(response)
                jobs.append(imported["job_id"])
            with (root / "logs/library-import.log").open("a") as log:
                log.write(
                    json.dumps(
                        {
                            "time": datetime.now(UTC).isoformat(),
                            "video_id": imported["video_id"],
                            "job_id": imported["job_id"],
                            "status": imported["status"],
                        }
                    )
                    + "\n"
                )
        if not jobs:
            raise RuntimeError("licensed_library_import_did_not_enqueue_a_job")
        state["library_job_ids"] = jobs
        save("wait_licensed_library_indexing")
        deadline = time.monotonic() + 1800
        for job_id in jobs:
            while time.monotonic() < deadline:
                with opener.open(
                    f"http://127.0.0.1:18960/api/v1/jobs/{job_id}", timeout=20
                ) as response:
                    job = json.load(response)
                if job["status"] == "succeeded":
                    break
                if job["status"] in {"failed", "cancelled", "dead_letter"}:
                    raise RuntimeError("licensed_library_indexing_failed")
                time.sleep(3)
            else:
                raise TimeoutError("licensed_library_indexing_deadline")
        browser_env = {
            **os.environ,
            "CAMCAT_E2E_VIDEO": str(root / "runtime/acceptance/source.mp4"),
            "CAMCAT_E2E_IMAGE": str(root / "runtime/acceptance/reference.jpg"),
            "CAMCAT_E2E_GOAL_SECONDS": "4",
        }
        command(
            [*studio, "browser"],
            "browser_original_and_localization",
            "browser-real.log",
            timeout=5400,
            environment=browser_env,
        )
        state["state"] = "succeeded"
        save("complete")
    except Exception as error:
        state.update(state="failed", error_type=type(error).__name__)
        if isinstance(error, subprocess.CalledProcessError):
            state["returncode"] = error.returncode
        elif isinstance(error, RuntimeError):
            state["error_reason"] = str(error)
        elif isinstance(error, urllib.error.HTTPError):
            state["http_status"] = error.code
            try:
                body = json.loads(error.read(8192))
                detail = body.get("error", {})
                if isinstance(detail, dict) and isinstance(detail.get("message"), str):
                    state["error_reason"] = detail["message"][:300]
            except (ValueError, AttributeError):
                pass
        save(str(state["phase"]))
        print("Deployment stopped at the recorded phase; no automatic restart.", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
