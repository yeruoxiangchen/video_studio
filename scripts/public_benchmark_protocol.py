"""Small, explicit adapters for public data; scoring stays with upstream verifiers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SOURCE = Path(__file__).resolve().parents[1]
QUERY_INSTRUCTION = "Find a video that contains the following visual content."


def select_msrvtt_pilot(
    rows: list[dict[str, Any]], *, queries: int, candidates: int, seed: int
) -> dict[str, Any]:
    if not 0 < queries <= candidates <= len(rows):
        raise ValueError("Require 0 < queries <= candidates <= available rows")
    ids: set[str] = set()
    for row in rows:
        identifier = row.get("video_id", "")
        if identifier in ids:
            raise ValueError("duplicate video ID")
        if not re.fullmatch(r"video\d+", identifier):
            raise ValueError("invalid video ID")
        if row.get("video") != identifier + ".mp4":
            raise ValueError("invalid video filename")
        if not isinstance(row.get("caption"), str) or not row["caption"].strip():
            raise ValueError("missing query caption")
        ids.add(identifier)

    def order(row: dict[str, Any], purpose: str) -> str:
        return hashlib.sha256(f"{seed}:{purpose}:{row['video_id']}".encode()).hexdigest()

    chosen = sorted(rows, key=lambda row: order(row, "corpus"))[:candidates]
    selected_queries = sorted(chosen, key=lambda row: order(row, "queries"))[:queries]
    return {
        "benchmark": "MMEB-v2/MSR-VTT/test_1k",
        "scope": "public-dataset pilot subset; not the full test_1k score",
        "selection": "sha256(seed:purpose:video_id), ascending; independent of model results",
        "seed": seed,
        "candidates": [{"video_id": row["video_id"], "filename": row["video"]} for row in chosen],
        "queries": [
            {
                "query_id": row["video_id"],
                "text": row["caption"],
                "instruction": QUERY_INSTRUCTION,
                "labels": [row["video_id"]],
            }
            for row in selected_queries
        ],
    }


def validate_rankings(subset: dict[str, Any], rankings: dict[str, list[str]]) -> None:
    query_ids = {q["query_id"] for q in subset["queries"]}
    if query_ids != set(rankings):
        raise ValueError("rankings do not cover the exact query set")
    candidate_ids = {item["video_id"] for item in subset["candidates"]}
    for query_id, ranking in rankings.items():
        if len(ranking) != len(candidate_ids) or set(ranking) != candidate_ids:
            raise ValueError("ranking has missing, duplicate or unknown candidate: " + query_id)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


class BenchmarkRun:
    def __init__(self, root: Path, kind: str, *, config_path: Path | None = None) -> None:
        identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
        self.output = root / "runs" / kind / identifier
        self.output.mkdir(parents=True)
        self.latest = self.output.parent / "latest.json"
        self.started = time.monotonic()
        self.report: dict[str, Any] = {
            "run_id": identifier,
            "pid": os.getpid(),
            "kind": kind,
            "output": str(self.output),
            "state": "running",
            "phase": "preflight",
            "started_at": datetime.now(UTC).isoformat(),
            "config_sha256": sha256(config_path or SOURCE / "eval/public_pilot_v1.json"),
            "automatic_restart": False,
        }
        signal.signal(signal.SIGTERM, self.cancel)
        signal.signal(signal.SIGINT, self.cancel)
        self.save()

    def save(self, **changes: Any) -> None:
        self.report.update(changes)
        self.report.update(
            updated_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=round(time.monotonic() - self.started, 3),
        )
        write_json(self.output / "report.json", self.report)
        write_json(self.latest, self.report)
        print(
            json.dumps(
                {k: self.report[k] for k in ("run_id", "state", "phase", "elapsed_seconds")}
                | changes,
                ensure_ascii=False,
            ),
            flush=True,
        )

    def cancel(self, number: int, _frame: Any) -> None:
        self.save(state="cancelled", phase="cancelled")
        raise SystemExit(128 + number)


def verify_receipts(root: Path, *, receipts_path: Path | None = None) -> None:
    for relative, expected in json.loads(
        (receipts_path or root / "upstream-receipts.json").read_text()
    ).items():
        if sha256(root / relative) != expected:
            raise ValueError("Upstream benchmark source changed: " + relative)


def transport_agentic_brief(brief: str) -> tuple[str, list[str]]:
    """Fit the public task into the API, preserving its storyboard and delivery rules."""
    notes = []
    if len(brief) > 6800:
        compact = re.sub(
            r"## Shot Language Reference\n.*?(?=## Storyboard\n)", "", brief, flags=re.S
        )
        if compact != brief:
            notes.append(
                "Generic shot-language glossary omitted to fit the 8000-character API limit; "
                "full original brief archived. Storyboard and delivery rules retained."
            )
            brief = compact
    instruction = brief + (
        "\n\nCamCat API transport: filenames map to the uploaded source IDs in the project. "
        "The server renders the saved timeline and derives solution.json from it. "
        "Use only these uploaded clips, each picked clip in full, at original speed; "
        "retain the picture and original audio. No extra captions, music or transitions. "
        "外部素材0%。按原题的片段数量、顺序及完整原片范围生成可直接导出的时间线。"
    )
    if len(instruction) > 8000:
        raise ValueError("Task brief exceeds the 8000-character CamCat API limit")
    return instruction, notes


def manifest_from_saved_state(
    state: dict[str, Any], source_names: dict[str, str]
) -> dict[str, Any]:
    clips = sorted(
        (
            clip
            for track in state["timeline"]["tracks"]
            if track["type"] == "video"
            for clip in track["segments"]
        ),
        key=lambda clip: clip["timeline_start_us"],
    )
    result = []
    for clip in clips:
        source = source_names.get(clip["source_id"])
        if source is None:
            raise ValueError("Saved timeline references a source outside the public task")
        start = clip["timeline_start_us"] / 1_000_000
        source_start = clip["source_start_us"] / 1_000_000
        result.append(
            {
                "source": Path(source).stem,
                "output": [start, start + clip["timeline_duration_us"] / 1_000_000],
                "source_range": [
                    source_start,
                    source_start + clip["source_duration_us"] / 1_000_000,
                ],
            }
        )
    return {"segments": result}


def oracle_command(workspace: Path, materials: Path, script: Path, media_tools: Path) -> list[str]:
    # Leave bwrap's temporary root writable so /workspace and /oracle can be created.
    # A read-only bind of the host root prevents these missing mountpoints being made.
    command = ["bwrap", "--die-with-parent", "--unshare-net"]
    for directory in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(directory).exists():
            command.extend(["--ro-bind", directory, directory])
    command.extend(["--dir", "/tools"])
    for name in ("ffmpeg", "ffprobe"):
        command.extend(["--ro-bind", str(media_tools / name), "/tools/" + name])
    command.extend(
        [
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/workspace",
            "--bind",
            str(workspace),
            "/workspace",
            "--ro-bind",
            str(materials),
            "/workspace/materials",
            "--dir",
            "/oracle",
            "--ro-bind",
            str(script),
            "/oracle/solve.sh",
            "--setenv",
            "PATH",
            "/tools:/usr/bin:/bin",
            "--setenv",
            "TMPDIR",
            "/workspace/work",
            "--",
            "/bin/bash",
            "/oracle/solve.sh",
        ]
    )
    return command
