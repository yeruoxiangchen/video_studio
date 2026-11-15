"""Inspect actual source boundaries for supported chronology constraints; no grader inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from scripts.public_benchmark_protocol import write_json
from scripts.run_localization_public_eval import configure_host


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="agentic-vbench-sequencing-task1")
    parser.add_argument("--confirm-existing", action="store_true")
    parser.add_argument("--production", action="store_true")
    args = parser.parse_args()
    configure_host()
    from camcat.agent.narrative import (
        BoundaryCaptions,
        NarrativeFacts,
        PairStateComparison,
        narrative_brief,
        narrative_sample_times,
        subtitle_blocks,
    )
    from camcat.agent.narrative_prompts import PAIR_SYSTEM
    from camcat.config import get_settings
    from camcat.media.ffmpeg import extract_analysis_frame
    from camcat.services.narrative import NativeNarrativeClient, _pictures

    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    os.environ["PATH"] = str(root / "tools/ffmpeg") + os.pathsep + os.environ["PATH"]
    run = root / "benchmarks/runs/editing_repair/20261115T011040Z-3651195"
    folder = run / args.task
    project = json.loads((folder / "before.json").read_text())["state"]["project_id"]
    for p in (root / "runtime/planning-evidence").glob("*/observations.json"):
        proof = json.loads(p.read_text())
        if proof["project_id"] == project:
            evidence = p.parent
            break
    else:
        raise ValueError("The original observation evidence is required")
    captions = {
        n: BoundaryCaptions.model_validate(v)
        for n, v in json.loads((evidence / "boundary-captions.json").read_text()).items()
    }
    chains = subtitle_blocks(set(proof["sources"]), captions)
    blocks = {"block_" + hashlib.sha256("|".join(c).encode()).hexdigest()[:10]: c for c in chains}
    manifest = json.loads((run / "inputs.json").read_text())
    task = next(t for t in manifest["tasks"] if t["id"] == args.task)
    out = root / "checks/quality_repair_20261114/narrative-continuity" / args.task
    out.mkdir(parents=True, exist_ok=True)
    if args.production:
        from camcat.services.narrative_review import review_narrative

        out = out / "production"
        out.mkdir(parents=True, exist_ok=True)
        all_frames = {}
        for name, value in proof["sources"].items():
            samples = []
            for index, at in enumerate(narrative_sample_times(value["duration_seconds"])):
                path = out / f"{name}.{index}.jpg"
                extract_analysis_frame(Path(task["materials"]) / name, path, at=at)
                samples.append((path, at))
            all_frames[name] = samples
        client = NativeNarrativeClient(get_settings())
        try:
            result = review_narrative(
                client,
                brief=narrative_brief((folder / "submitted-instruction.md").read_text()),
                facts={
                    n: NarrativeFacts.model_validate(v["facts"])
                    for n, v in proof["sources"].items()
                },
                captions=captions,
                frames=all_frames,
                chains=chains,
                draft=json.loads((evidence / "selection.json").read_text())["source_aliases"],
                audit=out,
            )
            write_json(out / "component-sequence.json", {"source_aliases": result})
            print(
                json.dumps({"sequence": result, "audit": str(out)}, ensure_ascii=False), flush=True
            )
        finally:
            client.client.close()
        return
    cards = {}
    for block, names in blocks.items():
        parts = [
            {
                "type": "text",
                "text": json.dumps({"block_id": block, "sources_in_fixed_order": list(names)}),
            }
        ]
        for name in names:
            source = proof["sources"][name]
            parts.append(
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "source": name,
                            "observations": source["facts"],
                            "subtitles": captions[name].model_dump(),
                        }
                    ),
                }
            )
            frames = []
            for label, at in [("opening", 0.05), ("closing", source["duration_seconds"] - 0.05)]:
                p = out / (name + "." + label + ".jpg")
                extract_analysis_frame(Path(task["materials"]) / name, p, at=at)
                frames.append((p, at))
            parts.extend(_pictures(frames, "Actual source opening/closing"))
        cards[block] = parts
    draft = json.loads((evidence / "block-proposal-1.json").read_text())["blocks"]
    context = {
        "story_overview": narrative_brief((folder / "submitted-instruction.md").read_text()),
        "draft_block_order": draft,
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "constraints": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "earlier_block": {"type": "string", "enum": list(blocks)},
                        "later_block": {"type": "string", "enum": list(blocks)},
                        "visible_evidence": {"type": "string", "maxLength": 512},
                        "certainty": {"type": "string", "enum": ["clear", "uncertain"]},
                    },
                    "required": ["earlier_block", "later_block", "visible_evidence", "certainty"],
                },
            }
        },
        "required": ["constraints"],
    }
    client = NativeNarrativeClient(get_settings())
    reviews = []
    try:
        if args.confirm_existing:
            reviews = json.loads((out / "reviews.json").read_text())
        for order in (
            () if args.confirm_existing else (sorted(blocks), sorted(blocks, reverse=True))
        ):
            content = [{"type": "text", "text": json.dumps(context)}]
            for b in order:
                content.extend(cards[b])
            result = client.complete(
                system=(
                    "Audit the draft using ACTUAL opening/closing frames and source observations. "
                    "Report only clear pairwise chronology constraints that the draft VIOLATES. "
                    "Identify specific actors, objects, scene transitions or linked dialogue. "
                    "Check whether entry into a location follows events already inside it. "
                    "A draft may be complete yet causally reversed. The synopsis may include "
                    "events outside this excerpt. Blood or similar actions in different rooms "
                    "are independent events. Do not invent offscreen causes. "
                    "Each block is indivisible, linked by a unique boundary subtitle. "
                    "An empty constraints array is correct when no supported violation is visible. "
                    "Block IDs and presentation order are arbitrary. Use uncertain for guesses; "
                    "only clear observations can correct order."
                ),
                user=content,
                schema=schema,
                max_tokens=2600,
                thinking=True,
                reasoning_tokens=1024,
                temperature=0,
                presence_penalty=0,
            )
            reviews.append(result)
            write_json(out / "reviews.json", reviews)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        write_json(out / "blocks.json", {"blocks": blocks, "draft": draft})
        ranks = {name: i for i, name in enumerate(draft)}
        suspects = sorted(
            {
                (r["earlier_block"], r["later_block"])
                for review in reviews
                for r in review["constraints"]
                if r["certainty"] == "clear" and ranks[r["earlier_block"]] > ranks[r["later_block"]]
            }
        )[:3]
        for index, pair in enumerate(suspects):
            comparisons = []
            for order in (pair, pair[::-1]):
                content = [
                    {
                        "type": "text",
                        "text": json.dumps({"story_overview": context["story_overview"]}),
                    }
                ]
                for label, block in zip(("A", "B"), order, strict=True):
                    content.append(
                        {
                            "type": "text",
                            "text": f"Block {label}; source order is fixed within this block",
                        }
                    )
                    for name in blocks[block]:
                        source = proof["sources"][name]
                        content.append(
                            {
                                "type": "text",
                                "text": json.dumps({"source": name, "facts": source["facts"]}),
                            }
                        )
                        samples = []
                        for j, fraction in enumerate((0.01, 0.33, 0.67, 0.99)):
                            at = fraction * source["duration_seconds"]
                            path = out / (name + f".pair-{j}.jpg")
                            extract_analysis_frame(Path(task["materials"]) / name, path, at=at)
                            samples.append((path, at))
                        content.extend(_pictures(samples, "Actual source frame"))
                raw = client.complete(
                    system=PAIR_SYSTEM,
                    user=content,
                    schema=PairStateComparison.model_json_schema(),
                    max_tokens=2400,
                    thinking=True,
                    reasoning_tokens=512,
                    temperature=0,
                    presence_penalty=0,
                )
                comparisons.append({"order": order, "result": raw})
                write_json(out / f"pair-{index}.json", comparisons)
            print(
                json.dumps({"pair": pair, "comparisons": comparisons}, ensure_ascii=False),
                flush=True,
            )
    finally:
        client.client.close()


if __name__ == "__main__":
    main()
