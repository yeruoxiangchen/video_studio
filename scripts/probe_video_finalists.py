"""Real high-resolution review of the finalists selected by the official video model."""

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
    from camcat.services.narrative import NativeNarrativeClient
    from camcat.services.storyboard_review import (
        apply_pairwise_review,
        compare_candidates,
        source_motion_card,
        video_assessments,
    )

    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    client = NativeNarrativeClient(get_settings())
    out = root / "checks/quality_repair_20261114/video-finalists"
    try:
        for run, task, positions in (
            ("20261115T002020Z-3479418", "agentic-vbench-assembly-task2", (3,)),
            ("20261114T232147Z-3074208", "agentic-vbench-assembly-task1", (3, 4)),
        ):
            folder = root / "benchmarks/runs/editing_repair" / run / task
            manifest = json.loads((folder.parent / "inputs.json").read_text())
            spec = next(t for t in manifest["tasks"] if t["id"] == task)
            slots = storyboard_slots((folder / "submitted-instruction.md").read_text())
            for slot in slots:
                if slot.position not in positions:
                    continue
                video = json.loads(
                    (
                        root
                        / "checks/quality_repair_20261114/direct-video-reranker-full"
                        / task
                        / f"slot-{slot.position}.json"
                    ).read_text()
                )
                rows = video_assessments(
                    [r["name"] for r in video["sources"]], [r["score"] for r in video["sources"]]
                )
                pair = [
                    r["material_id"] for r in sorted(rows, key=lambda r: r["fit"], reverse=True)[:2]
                ]
                cards = {
                    n: source_motion_card(Path(spec["materials"]) / n, n, width=768) for n in pair
                }
                judgments = compare_candidates(client, slot, pair, cards)
                output = apply_pairwise_review(rows, pair, judgments)
                result = {
                    "task": task,
                    "slot": slot.position,
                    "pair": pair,
                    "judgments": judgments,
                    "final_ranking": output,
                }
                write_json(out / task / f"slot-{slot.position}.json", result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        client.client.close()


if __name__ == "__main__":
    main()
