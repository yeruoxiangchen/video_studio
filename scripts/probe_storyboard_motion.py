"""Review close visual candidates using measured background motion; no grader inputs."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

import cv2
import numpy as np

from scripts.public_benchmark_protocol import write_json
from scripts.run_localization_public_eval import configure_host


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="agentic-vbench-assembly-task2")
    parser.add_argument("--production-review", action="store_true")
    parser.add_argument("--all-candidates", action="store_true")
    parser.add_argument("--run", default="20261114T232147Z-3074208")
    parser.add_argument("--positions", default="")
    args = parser.parse_args()
    configure_host()
    from camcat.agent.storyboard import storyboard_slots
    from camcat.config import get_settings
    from camcat.services.narrative import NativeNarrativeClient
    from camcat.services.storyboard_review import compare_candidates, source_motion_card

    from services.local_models.camera_motion import background_motion

    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    run = root / "benchmarks/runs/editing_repair" / args.run
    task = next(
        t for t in json.loads((run / "inputs.json").read_text())["tasks"] if t["id"] == args.task
    )
    analysis = json.loads((run / task["id"] / "analysis.json").read_text())["result"]
    ids = {m["media_id"] for m in analysis["source_media"]}
    proof = None
    for f in (root / "runtime/planning-evidence").glob("*/storyboard-review.json"):
        value = json.loads(f.read_text())
        if {x["source_id"] for x in value.get("sources", {}).values()} == ids:
            proof = value
            break
    if proof is None:
        raise ValueError("The original image-review evidence is required")
    slots = storyboard_slots((run / task["id"] / "submitted-instruction.md").read_text())
    out = root / "checks/quality_repair_20261114" / ("motion-probe-" + args.task)
    if args.production_review:
        out = out.with_name(out.name + "-production")
    if args.all_candidates:
        out = out.with_name(out.name + "-all-candidates")
    if args.run != "20261114T232147Z-3074208":
        out = out.with_name(out.name + "-" + args.run)
    out.mkdir(parents=True, exist_ok=True)
    client = NativeNarrativeClient(get_settings())
    results = []
    try:
        for slot, rows in zip(slots, proof["assessments"], strict=True):
            if args.positions and str(slot.position) not in args.positions.split(","):
                continue
            ordered = sorted(
                (r for r in rows if not r["contradiction"]), key=lambda r: r["fit"], reverse=True
            )
            if not args.all_candidates and (
                len(ordered) < 2 or ordered[0]["fit"] - ordered[1]["fit"] > 15
            ):
                continue
            names = [r["material_id"] for r in (rows if args.all_candidates else ordered[:2])]
            if args.production_review:
                cards = {
                    name: source_motion_card(Path(task["materials"]) / name, name) for name in names
                }
                judgments = compare_candidates(client, slot, names, cards)
                results.append({"slot": slot.position, "judgments": judgments})
                write_json(out / f"slot-{slot.position}.json", judgments)
                print(json.dumps(results[-1], ensure_ascii=False), flush=True)
                continue
            cards = {}
            for name in names:
                cap = cv2.VideoCapture(str(Path(task["materials"]) / name))
                count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                frames = []
                pictures = []
                for index in np.unique(np.linspace(0, count - 1, 16).round().astype(int)):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(index))
                    ok, frame = cap.read()
                    if not ok:
                        raise ValueError("Cannot read an actual source frame")
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    frames.append(rgb)
                cap.release()
                profile = background_motion(np.stack(frames))
                for j in (0, 4, 8, 12, 15):
                    frame = cv2.cvtColor(frames[j], cv2.COLOR_RGB2BGR)
                    frame = cv2.resize(frame, (768, round(frame.shape[0] * 768 / frame.shape[1])))
                    ok, jpg = cv2.imencode(".jpg", frame)
                    assert ok
                    pictures.extend(
                        [
                            {"type": "text", "text": f"{name}: frame {j + 1}/16"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/jpeg;base64,"
                                    + base64.b64encode(jpg.tobytes()).decode()
                                },
                            },
                        ]
                    )
                cards[name] = {"profile": profile, "pictures": pictures}
                write_json(out / (name + ".motion.json"), profile)
            judgments = []
            for order in (names, names[::-1]):
                content = [
                    {
                        "type": "text",
                        "text": json.dumps({"slot": slot.model_dump()}, ensure_ascii=False),
                    }
                ]
                for name in order:
                    p = cards[name]["profile"]
                    compact = {
                        k: p[k]
                        for k in (
                            "scale_change",
                            "valid_pairs",
                            "total_pairs",
                            "median_inlier_fraction",
                        )
                    }
                    compact["center_displacements"] = [
                        [s["center_dx"], s["center_dy"], s["scale"]] for s in p["steps"]
                    ]
                    content.append(
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"source": name, "measured_background_motion": compact}
                            ),
                        }
                    )
                    content.extend(cards[name]["pictures"])
                result = client.complete(
                    system=(
                        "Compare these two source clips against the exact storyboard. "
                        "Compare OPENING framing with shot_size: a later zoom out does not "
                        "make a close opening an extreme-wide opening. Compare the subject's "
                        "initial orientation explicitly in image-left/image-right coordinates. "
                        "Then distinguish zoom/pullback, vertical boom/tilt, and lateral motion. "
                        "The actual peripheral LK/RANSAC measurements are image motion, not "
                        "camera pose: shrinking scale supports outward apparent movement; "
                        "positive image dx moves background right. Judge reliability and visible "
                        "frames together. Never invent unseen details or a movie identity. "
                        "Prefer the clip matching more defining storyboard details. Be explicit "
                        "about opening framing and direction rather than treating similar scenes "
                        "as identical. Candidate order carries no meaning."
                    ),
                    user=content,
                    schema={
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "preferred": {"type": "string", "enum": names},
                            "opening_comparison": {"type": "string", "maxLength": 400},
                            "motion_comparison": {"type": "string", "maxLength": 400},
                            "confidence": {
                                "type": "string",
                                "enum": ["clear", "weak", "uncertain"],
                            },
                        },
                        "required": [
                            "preferred",
                            "opening_comparison",
                            "motion_comparison",
                            "confidence",
                        ],
                    },
                    max_tokens=1800,
                    thinking=True,
                    reasoning_tokens=512,
                    temperature=0,
                    presence_penalty=0,
                )
                judgments.append(result)
                write_json(out / f"slot-{slot.position}.json", judgments)
                print(
                    json.dumps({"slot": slot.position, "judgment": result}, ensure_ascii=False),
                    flush=True,
                )
            results.append({"slot": slot.position, "judgments": judgments})
        write_json(out / "results.json", results)
    finally:
        client.client.close()


if __name__ == "__main__":
    main()
