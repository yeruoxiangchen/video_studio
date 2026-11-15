"""Source-bound observable events for reconstruction of an uploaded narrative."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Any, Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field


class NarrativeFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actors: list[str] = Field(max_length=4)
    opening_state: str = Field(min_length=1)
    events: list[str] = Field(min_length=1, max_length=6)
    closing_state: str = Field(min_length=1)
    location: str = Field(min_length=1)
    visible_dialogue: list[str] = Field(default_factory=list)


class BoundaryCaptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    opening_text: str = Field(default="", max_length=256)
    closing_text: str = Field(default="", max_length=256)


class AppearanceState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    face_and_eye_features: str = Field(max_length=384)
    face_covering: str = Field(max_length=384)
    held_objects: str = Field(max_length=384)
    location: str = Field(max_length=384)
    physical_condition: str = Field(max_length=384)


class PairStateComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    A_state: AppearanceState
    B_state: AppearanceState
    same_physical_scene: Literal["yes", "no", "uncertain"]
    observed_causal_link: str = Field(max_length=384)
    likely_earlier: Literal["A", "B", "uncertain"]
    certainty: Literal["clear", "weak", "unknown"]


def narrative_brief(instruction: str) -> str:
    tokens = MarkdownIt("commonmark").parse(instruction)
    lines = instruction.splitlines()
    for i, token in enumerate(tokens):
        if token.type != "heading_open" or token.level != 0:
            continue
        title = tokens[i + 1].content.strip().casefold().rstrip(":：")
        if title not in {"story overview", "synopsis", "plot", "故事梗概", "剧情概述"}:
            continue
        if token.map is None:
            continue
        end = len(lines)
        for following in tokens[i + 2 :]:
            if (
                following.type == "heading_open"
                and following.level == 0
                and int(following.tag[1:]) <= int(token.tag[1:])
                and following.map is not None
            ):
                end = following.map[0]
                break
        if body := "\n".join(lines[token.map[1] : end]).strip():
            return body
    return instruction.strip()


def narrative_ordering_requested(instruction: str) -> bool:
    return narrative_brief(instruction) != instruction.strip() or bool(
        re.search(r"chronolog|narrative|故事|剧情|时间顺序", instruction, re.I)
    )


def expand_block_order(
    order: list[str], blocks: dict[str, tuple[str, ...]], precedence: list[tuple[str, str]]
) -> list[str]:
    if len(order) != len(blocks) or len(set(order)) != len(order) or set(order) != blocks.keys():
        raise ValueError("narrative blocks must each appear exactly once")
    sequence = [source for block in order for source in blocks[block]]
    if len(sequence) != len(set(sequence)):
        raise ValueError("narrative blocks contain a repeated source")
    positions = {source: i for i, source in enumerate(sequence)}
    if any(
        before not in positions or after not in positions or positions[before] >= positions[after]
        for before, after in precedence
    ):
        raise ValueError("narrative order contradicts a confirmed actor-state correction")
    return sequence


def _caption_key(text: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    words = re.findall(r"\w+", normalized)
    key = " ".join(words)
    if len(key.replace(" ", "")) < 8:
        return None
    if len(words) < 3 and len(re.findall(r"[\u3400-\u9fff]", key)) < 8:
        return None
    return key


def subtitle_blocks(
    source_ids: set[str], captions: dict[str, BoundaryCaptions]
) -> tuple[tuple[str, ...], ...]:
    """Join only unique, nontrivial end/start captions; leave ambiguous cycles separate."""
    if captions.keys() - source_ids:
        raise ValueError("caption evidence references an unknown source")
    opening: dict[str, list[str]] = {}
    closing: dict[str, list[str]] = {}
    for source_id, value in captions.items():
        for text, destination in (
            (value.opening_text, opening),
            (value.closing_text, closing),
        ):
            if key := _caption_key(text):
                destination.setdefault(key, []).append(source_id)
    following = {
        sources[0]: opening[key][0]
        for key, sources in closing.items()
        if len(sources) == len(opening.get(key, [])) == 1 and sources[0] != opening[key][0]
    }
    blocks: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for first in sorted(source_ids - set(following.values())):
        current = first
        chain = []
        while current not in seen:
            seen.add(current)
            chain.append(current)
            if current not in following:
                break
            current = following[current]
        blocks.append(tuple(chain))
    # A repeated line can form a cycle without establishing chronology.
    blocks.extend((source_id,) for source_id in sorted(source_ids - seen))
    return tuple(blocks)


def narrative_sample_times(duration: float) -> list[float]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("narrative source duration must be finite and positive")
    count = min(16, max(8, int(duration) + 1))
    margin = min(0.05, duration / 4)
    return [margin + (duration - 2 * margin) * i / (count - 1) for i in range(count)]


def narrative_materials(
    sources: dict[str, NarrativeFacts],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    materials: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    for source_id, facts in sorted(sources.items()):
        alias = "source_" + hashlib.sha256(source_id.encode()).hexdigest()[:10]
        if alias in aliases:
            raise ValueError("narrative source alias collision")
        aliases[alias] = source_id
        evidence = []
        for key in (
            "actors",
            "location",
            "opening_state",
            "events",
            "closing_state",
            "visible_dialogue",
        ):
            value = getattr(facts, key)
            for index, text in enumerate(value if isinstance(value, list) else [value]):
                evidence.append({"id": f"{alias}_{key}_{index}", "kind": key, "text": str(text)})
        materials.append({"material_id": alias, "evidence": evidence})
    materials.sort(key=lambda m: m["material_id"])
    return materials, aliases
