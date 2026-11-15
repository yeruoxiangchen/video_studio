"""Measure real read routes on one fixed fixture and profile the backend against Postgres."""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import hashlib
import io
import json
import os
import pstats
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from measure_reliability import percentile

ROOT = Path(os.environ.get("CAMCAT_STUDIO_DATA", "/data/zjr/camcat_studio"))
OUT = ROOT / "checks/deep_optimization_20261111"
SOURCE = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:18960"


def normalized(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        url = urlsplit(value)
        return urlunsplit((url.scheme, url.netloc, url.path, "", ""))
    return value


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(normalized(value), sort_keys=True).encode()).hexdigest()


def prepare(source_job: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "read-fixture.json"
    if path.exists():
        print(path.read_text())
        return
    with httpx.Client(base_url=API, trust_env=False, timeout=60) as client:
        project = client.post("/api/v1/projects", json={"name": "固定读接口性能样本-20261111"})
        project.raise_for_status()
        created = client.post(
            "/api/v1/editing/sessions",
            json={
                "project_id": project.json()["project_id"],
                "source_job_id": source_job,
                "current_goal": "固定数据集读接口性能对照",
            },
        )
        created.raise_for_status()
        session = created.json()
    packet = {
        "project_id": project.json()["project_id"],
        "session_id": session["editing_session_id"],
        "source_job_id": source_job,
        "version": session["state_version"],
    }
    path.write_text(json.dumps(packet, indent=2) + "\n")
    print(json.dumps(packet))


async def measure(label: str) -> None:
    fixture = json.loads((OUT / "read-fixture.json").read_text())
    path = "/api/v1/editing/sessions/" + fixture["session_id"]
    routes = {
        "projects": "/api/v1/projects?include_sessions=true&limit=10",
        "jobs": "/api/v1/jobs?limit=10",
        "session": path,
        "localization": path + "/localization",
    }
    report: dict[str, Any] = {"label": label, "fixture": fixture, "routes": []}
    async with httpx.AsyncClient(
        base_url=API, trust_env=False, timeout=60, limits=httpx.Limits(max_connections=32)
    ) as client:
        for name, route in routes.items():
            first = await client.get(route)
            first.raise_for_status()
            digest = fingerprint(first.json())
            for concurrency in (1, 16):
                semaphore = asyncio.Semaphore(concurrency)

                async def one(
                    limiter: asyncio.Semaphore = semaphore, endpoint: str = route
                ) -> tuple[int, float, int]:
                    async with limiter:
                        start = time.monotonic()
                        response = await client.get(endpoint)
                        return (
                            response.status_code,
                            (time.monotonic() - start) * 1000,
                            len(response.content),
                        )

                started = time.monotonic()
                outcomes = await asyncio.gather(*(one() for _ in range(64)))
                elapsed = time.monotonic() - started
                statuses = Counter(status for status, _, _ in outcomes)
                times = [duration for _, duration, _ in outcomes]
                packet = {
                    "name": name,
                    "concurrency": concurrency,
                    "requests": len(outcomes),
                    "statuses": dict(statuses),
                    "rps": round(len(outcomes) / elapsed, 2),
                    "p50_ms": percentile(times, 0.5),
                    "p95_ms": percentile(times, 0.95),
                    "p99_ms": percentile(times, 0.99),
                    "response_bytes": outcomes[0][2],
                    "canonical_sha256": digest,
                }
                report["routes"].append(packet)
                print(json.dumps(packet), flush=True)
                if statuses != {200: 64}:
                    raise RuntimeError("Read profile returned unexpected HTTP statuses")
            last = await client.get(route)
            last.raise_for_status()
            if fingerprint(last.json()) != digest:
                raise RuntimeError("Dataset changed during route profile: " + name)
    (OUT / (label + "-reads.json")).write_text(json.dumps(report, indent=2) + "\n")


def backend_profile(label: str) -> None:
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url

    for key, value in dotenv_values(SOURCE / ".env").items():
        if value is not None:
            os.environ[key] = value
    database = make_url(os.environ["CAMCAT_DATABASE_URL"]).set(host="127.0.0.1", port=18970)
    os.environ.update(
        CAMCAT_DATABASE_URL=database.render_as_string(False),
        CAMCAT_OBJECT_STORE_ENDPOINT="http://127.0.0.1:18963",
        CAMCAT_MILVUS_URI="http://127.0.0.1:18965",
    )
    from camcat import api
    from camcat.database import SessionLocal, engine
    from sqlalchemy import event

    fixture = json.loads((OUT / "read-fixture.json").read_text())
    from uuid import UUID

    sql: dict[str, Any] = {"queries": 0, "seconds": 0.0}

    @event.listens_for(engine, "before_cursor_execute")
    def before(
        _conn: Any, _cursor: Any, _statement: Any, _params: Any, context: Any, _many: Any
    ) -> None:
        context._profile_started = time.monotonic()

    @event.listens_for(engine, "after_cursor_execute")
    def after(
        _conn: Any, _cursor: Any, _statement: Any, _params: Any, context: Any, _many: Any
    ) -> None:
        sql["queries"] += 1
        sql["seconds"] += time.monotonic() - context._profile_started

    operations = {
        "projects": lambda db: api.list_projects(
            db, api.settings.local_user_id, limit=10, include_sessions=True
        ),
        "jobs": lambda db: api.list_jobs(db, api.settings.local_user_id, limit=10),
        "session": lambda db: api.get_editing_session(
            UUID(fixture["session_id"]), db, api.settings.local_user_id
        ),
    }
    for name, operation in operations.items():
        profiler = cProfile.Profile()
        sql.update(queries=0, seconds=0.0)
        started = time.monotonic()
        for _ in range(12):
            with SessionLocal() as db:

                def read(operation: Callable[[Any], Any] = operation) -> None:
                    result = operation(db)
                    json.dumps(
                        result.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
                    )

                profiler.runcall(read)
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(24)
        (OUT / f"{label}-{name}-cpu.txt").write_text(stream.getvalue())
        print(
            json.dumps(
                {
                    "name": name,
                    "calls": 12,
                    "elapsed": time.monotonic() - started,
                    "sql": sql.copy(),
                }
            )
        )
        print(stream.getvalue())


def compare(serial_path: Path, split_path: Path) -> None:
    before = json.loads((OUT / "before-reads.json").read_text())
    after = json.loads((OUT / "after-signatures-reads.json").read_text())
    reads = []
    for old, new in zip(before["routes"], after["routes"], strict=True):
        assert (old["name"], old["concurrency"]) == (new["name"], new["concurrency"])
        assert old["canonical_sha256"] == new["canonical_sha256"], "Read fixture changed"
        reads.append(
            {
                "name": old["name"],
                "concurrency": old["concurrency"],
                "before_rps": old["rps"],
                "after_rps": new["rps"],
                "before_p95_ms": old["p95_ms"],
                "after_p95_ms": new["p95_ms"],
            }
        )
    serial, split = (json.loads(path.read_text()) for path in (serial_path, split_path))
    assert serial["state"] == split["state"] == "completed"
    assert serial["summary"]["failed_pipelines"] == split["summary"]["failed_pipelines"] == 0
    overlap = []
    pipelines = [pipeline for batch in split["batches"] for pipeline in batch["pipelines"]]
    for pipeline in pipelines:
        render = pipeline["render"]
        for other in pipelines:
            for name in ("translation", "dubbing", "cached_dubbing"):
                control = other[name]
                if control.get("includes_explicit_retry"):
                    continue
                start = max(
                    datetime.fromisoformat(render["started_at"]),
                    datetime.fromisoformat(control["started_at"]),
                )
                end = min(
                    datetime.fromisoformat(render["finished_at"]),
                    datetime.fromisoformat(control["finished_at"]),
                )
                if end > start:
                    overlap.append(
                        {
                            "render_job": render["job_id"],
                            "control_job": control["job_id"],
                            "control_kind": name,
                            "overlap_seconds": round((end - start).total_seconds(), 3),
                        }
                    )
    packet = {
        "reads": reads,
        "serial_report": str(serial_path),
        "split_report": str(split_path),
        "serial_summary": serial["summary"],
        "split_summary": split["summary"],
        "serial_batches": serial["batches"],
        "split_batches": split["batches"],
        "overlapping_execution": overlap,
    }
    (OUT / "comparison.json").write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in packet.items()
                if key not in {"serial_batches", "split_batches"}
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "measure", "backend", "compare"])
    parser.add_argument("--label", default="before")
    parser.add_argument("--source-job")
    parser.add_argument("--serial-report", type=Path)
    parser.add_argument("--split-report", type=Path)
    args = parser.parse_args()
    if args.mode == "prepare":
        if not args.source_job:
            parser.error("prepare requires a real --source-job")
        prepare(args.source_job)
    elif args.mode == "measure":
        asyncio.run(measure(args.label))
    elif args.mode == "compare":
        if not args.serial_report or not args.split_report:
            parser.error("compare requires --serial-report and --split-report")
        compare(args.serial_report, args.split_report)
    else:
        backend_profile(args.label)


if __name__ == "__main__":
    main()
