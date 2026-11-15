"""Verify draft chronology with original frames before moving any indivisible block."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from camcat.agent.continuity import backward_candidates, confirmed_pair_order, stable_block_order
from camcat.agent.narrative import (
    BoundaryCaptions,
    NarrativeFacts,
    PairStateComparison,
    expand_block_order,
)
from camcat.agent.narrative_prompts import PAIR_SYSTEM
from camcat.services.providers import ProviderOutputError

if TYPE_CHECKING:
    from camcat.services.narrative import NativeNarrativeClient

Frame = tuple[Path, float]

REVIEW_SYSTEM = (
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
)


def review_narrative(
    client: NativeNarrativeClient,
    *,
    brief: str,
    facts: dict[str, NarrativeFacts],
    captions: dict[str, BoundaryCaptions],
    frames: dict[str, list[Frame]],
    chains: tuple[tuple[str, ...], ...],
    draft: list[str],
    audit: Path,
) -> list[str]:
    from camcat.services.narrative import _pictures

    blocks = {"block_" + hashlib.sha256("|".join(c).encode()).hexdigest()[:10]: c for c in chains}
    ranks = {name: i for i, name in enumerate(draft)}
    order = sorted(blocks, key=lambda b: min(ranks[n] for n in blocks[b]))
    if expand_block_order(order, blocks, []) != draft:
        raise ProviderOutputError("Narrative review must preserve the original caption blocks")
    if len(blocks) < 2:
        return draft

    def save(name: str, value: Any) -> None:
        (audit / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    cards = {}
    for block, names in blocks.items():
        parts: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "block_id": block,
                        "sources_in_fixed_order": list(names),
                    }
                ),
            }
        ]
        for name in names:
            parts.append(
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "source": name,
                            "observations": facts[name].model_dump(),
                            "subtitles": captions[name].model_dump(),
                        }
                    ),
                }
            )
            parts.extend(
                _pictures([frames[name][0], frames[name][-1]], "Actual source opening/closing")
            )
        cards[block] = parts
    edge_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "earlier_block": {"type": "string", "enum": list(blocks)},
            "later_block": {"type": "string", "enum": list(blocks)},
            "visible_evidence": {"type": "string", "maxLength": 512},
            "certainty": {"type": "string", "enum": ["clear", "uncertain"]},
        },
        "required": ["earlier_block", "later_block", "visible_evidence", "certainty"],
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "constraints": {"type": "array", "maxItems": 5, "items": edge_schema},
        },
        "required": ["constraints"],
    }
    reviews = []
    for presentation in (sorted(blocks), sorted(blocks, reverse=True)):
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "story_overview": brief,
                        "draft_block_order": order,
                    }
                ),
            }
        ]
        for block in presentation:
            content.extend(cards[block])
        reviews.append(
            client.complete(
                system=REVIEW_SYSTEM,
                user=content,
                schema=schema,
                max_tokens=2600,
                thinking=True,
                reasoning_tokens=1024,
                temperature=0,
                presence_penalty=0,
            )
        )
        save("continuity-reviews.json", reviews)
    candidates = backward_candidates(reviews, order)
    confirmed = []
    for index, edge in enumerate(candidates):
        comparisons = []
        for pair_presentation in (edge, edge[::-1]):
            content = [{"type": "text", "text": json.dumps({"story_overview": brief})}]
            for label, block in zip(("A", "B"), pair_presentation, strict=True):
                content.append(
                    {
                        "type": "text",
                        "text": (f"Block {label}; source order is fixed within this block"),
                    }
                )
                for name in blocks[block]:
                    content.append(
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "source": name,
                                    "facts": facts[name].model_dump(),
                                }
                            ),
                        }
                    )
                    samples = frames[name]
                    selected = [
                        samples[round((len(samples) - 1) * f)] for f in (0, 1 / 3, 2 / 3, 1)
                    ]
                    content.extend(_pictures(selected, "Actual source frame"))
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
            PairStateComparison.model_validate(raw)
            comparisons.append({"order": pair_presentation, "result": raw})
            save(f"continuity-pair-{index}.json", comparisons)
        if confirmed_pair_order(comparisons) == edge:
            confirmed.append(edge)
    corrected = stable_block_order(order, confirmed)
    precedence = [(blocks[a][-1], blocks[b][0]) for a, b in confirmed]
    result = expand_block_order(corrected, blocks, precedence)
    save(
        "continuity-result.json",
        {
            "draft_block_order": order,
            "candidate_edges": candidates,
            "confirmed_edges": confirmed,
            "corrected_block_order": corrected,
            "source_aliases": result,
        },
    )
    return result
