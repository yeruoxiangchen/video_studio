"""Review finalists from archived, genuine visual rankings; no grader input."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts.public_benchmark_protocol import write_json
from scripts.run_localization_public_eval import configure_host


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reviews", required=True)
    parser.add_argument("--run", default="20261115T002020Z-3479418")
    parser.add_argument("--task", default="agentic-vbench-assembly-task2")
    parser.add_argument("--positions", default="2,3")
    args = parser.parse_args()
    configure_host()
    from camcat.agent.storyboard import storyboard_slots
    from camcat.config import get_settings
    from camcat.services.narrative import NativeNarrativeClient
    from camcat.services.storyboard_review import (
        apply_pairwise_review,
        compare_candidates,
        ranked_assessments,
        source_motion_card,
    )

    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    run = root / "benchmarks/runs/editing_repair" / args.run
    task = next(
        t for t in json.loads((run / "inputs.json").read_text())["tasks"] if t["id"] == args.task
    )
    ids = {
        m["media_id"]
        for m in json.loads((run / args.task / "analysis.json").read_text())["result"][
            "source_media"
        ]
    }
    for path in (root / "runtime/planning-evidence").glob("*/storyboard-review.json"):
        proof = json.loads(path.read_text())
        if {s["source_id"] for s in proof["sources"].values()} == ids:
            break
    else:
        raise ValueError("The original source-review evidence is required")
    slots = storyboard_slots((run / args.task / "submitted-instruction.md").read_text())
    out = root / "checks/quality_repair_20261114/finalists-v4" / args.task
    client = NativeNarrativeClient(get_settings())
    try:
        for slot, initial in zip(slots, proof["assessments"], strict=True):
            if str(slot.position) not in args.positions.split(","):
                continue
            reviews = json.loads((Path(args.reviews) / f"slot-{slot.position}.json").read_text())
            rows = ranked_assessments([r["material_id"] for r in initial], reviews)
            pair = [
                r["material_id"] for r in sorted(rows, key=lambda r: r["fit"], reverse=True)[:2]
            ]
            cards = {n: source_motion_card(Path(task["materials"]) / n, n, width=768) for n in pair}
            judgments = compare_candidates(client, slot, pair, cards)
            ranked_assessments(pair, judgments)
            rows = apply_pairwise_review(rows, pair, judgments)
            value = {
                "slot": slot.position,
                "pair": pair,
                "judgments": judgments,
                "final_ranking": rows,
            }
            write_json(out / f"slot-{slot.position}.json", value)
            print(json.dumps(value, ensure_ascii=False), flush=True)
    finally:
        client.client.close()


if __name__ == "__main__":
    main()
