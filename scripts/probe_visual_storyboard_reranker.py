"""Direct official video reranking on the actual retrieved pools; no oracle inputs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scripts.public_benchmark_protocol import write_json
from scripts.run_localization_public_eval import configure_host


def main() -> None:
    configure_host()
    from camcat.agent.storyboard import storyboard_slots
    from camcat.config import get_settings
    from camcat.services.object_store import ObjectStore
    from camcat.services.providers import QwenRerankerClient

    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    settings = get_settings()
    store, client = ObjectStore(settings), QwenRerankerClient(settings)
    out = root / "checks/quality_repair_20261114/direct-video-reranker-full"
    try:
        for run_id, task_id in (
            ("20261115T002020Z-3479418", "agentic-vbench-assembly-task2"),
            ("20261114T232147Z-3074208", "agentic-vbench-assembly-task1"),
        ):
            folder = root / "benchmarks/runs/editing_repair" / run_id / task_id
            media = json.loads((folder / "analysis.json").read_text())["result"]["source_media"]
            by_name = {m["filename"]: m for m in media}
            ids = {m["media_id"] for m in media}
            for path in (root / "runtime/planning-evidence").glob("*/storyboard-review.json"):
                proof = json.loads(path.read_text())
                if {s["source_id"] for s in proof["sources"].values()} == ids:
                    break
            else:
                raise ValueError("The original source review is required")
            slots = storyboard_slots((folder / "submitted-instruction.md").read_text())
            for slot, pool in zip(slots, proof["assessments"], strict=True):
                names = [r["material_id"] for r in pool]
                scores = client.rerank(
                    {"text": json.dumps(slot.model_dump(exclude_none=True), ensure_ascii=False)},
                    [{"video_url": store.signed_url(by_name[n]["storage_key"])} for n in names],
                )
                value = {
                    "task": task_id,
                    "slot": slot.position,
                    "sources": [
                        {"name": n, "score": s} for n, s in zip(names, scores, strict=True)
                    ],
                }
                write_json(out / task_id / f"slot-{slot.position}.json", value)
                print(json.dumps(value), flush=True)
    finally:
        client.client.close()


if __name__ == "__main__":
    main()
