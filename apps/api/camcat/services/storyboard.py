"""Rerank actual source videos, review finalists, and assign distinct originals."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from camcat.agent.errors import AgentInputError
from camcat.agent.sequence import SourceSequenceProposal, material_aliases, source_filenames
from camcat.agent.storyboard import StoryboardSlot, visual_storyboard_assignment
from camcat.config import Settings
from camcat.domain.project import EditingProjectV2
from camcat.media.ffmpeg import probe
from camcat.services.object_store import ObjectStore
from camcat.services.providers import QwenRerankerClient
from camcat.services.storyboard_review import (
    apply_pairwise_review,
    compare_candidates,
    ranked_assessments,
    source_motion_card,
    video_assessments,
)

if TYPE_CHECKING:
    from camcat.services.narrative import NativeNarrativeClient


def review_storyboard(
    client: NativeNarrativeClient,
    settings: Settings,
    store: ObjectStore,
    *,
    project: EditingProjectV2,
    slots: list[StoryboardSlot],
    originals: list[dict[str, Any]],
) -> SourceSequenceProposal:
    ids = {str(item["source_id"]) for item in originals}
    if not slots or not len(slots) <= len(ids) <= 16:
        raise AgentInputError(
            "分镜匹配需要足量原片，单次最多 16 段", code="storyboard_source_count"
        )
    aliases = material_aliases(ids, source_filenames(project))
    registry = {s.source_id: s for s in project.sources}
    observations = {str(m["source_id"]): m for m in originals}
    materials = [
        {**observations[source_id], "material_id": name}
        for name, source_id in sorted(aliases.items())
    ]
    root = Path(settings.runtime_dir)
    temporary = root / "narrative-tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    audit = root / "planning-evidence" / uuid4().hex
    audit.mkdir(parents=True, mode=0o700)
    proof: dict[str, Any] = {
        "model": client.model,
        "scope": "official direct-video reranking and counterbalanced finalist review",
        "manual_facts": False,
        "sources": {},
        "assessments": [],
        "visual_reviews": [],
        "pairwise_reviews": [],
    }

    def save() -> None:
        (audit / "storyboard-review.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2) + "\n"
        )

    with TemporaryDirectory(dir=temporary, prefix="storyboard-") as work:
        source_paths = {}
        for index, (name, source_id) in enumerate(sorted(aliases.items())):
            source = Path(work) / f"source-{index}.mp4"
            store.download_file(registry[source_id].storage_key, source)
            source_paths[name] = source
            metadata = probe(source)
            proof["sources"][name] = {
                "source_id": source_id,
                "duration_seconds": metadata.video_duration or metadata.duration,
            }
        save()
        assessments = []
        final_cards: dict[str, dict[str, Any]] = {}
        reranker = QwenRerankerClient(settings)
        try:
            for slot in slots:
                scores = reranker.rerank(
                    {"text": slot.description},
                    [{"text": m.get("description", "")} for m in materials],
                )
                candidates = sorted(range(len(materials)), key=lambda j: scores[j], reverse=True)[
                    :5
                ]
                names = [materials[j]["material_id"] for j in candidates]
                by_name = {m["material_id"]: m["source_id"] for m in materials}
                video_scores = reranker.rerank(
                    {"text": json.dumps(slot.model_dump(exclude_none=True), ensure_ascii=False)},
                    [
                        {"video_url": store.signed_url(registry[by_name[name]].storage_key)}
                        for name in names
                    ],
                )
                rows = video_assessments(names, video_scores)
                proof["visual_reviews"].append(
                    {
                        "position": slot.position,
                        "candidates": names,
                        "text_recall_scores": [scores[j] for j in candidates],
                        "direct_video_scores": video_scores,
                        "model": settings.reranker_model,
                    }
                )
                # Review the two strongest video matches at higher resolution.
                pair = [
                    r["material_id"] for r in sorted(rows, key=lambda r: r["fit"], reverse=True)[:2]
                ]
                if len(pair) == 2:
                    for name in pair:
                        if name not in final_cards:
                            final_cards[name] = source_motion_card(
                                source_paths[name], name, width=768
                            )
                            proof["sources"][name]["final_review_frames"] = final_cards[name][
                                "frames"
                            ]
                            proof["sources"][name]["background_motion"] = final_cards[name][
                                "profile"
                            ]
                    comparison = compare_candidates(client, slot, pair, final_cards)
                    ranked_assessments(pair, comparison)
                    rows = apply_pairwise_review(rows, pair, comparison)
                    proof["pairwise_reviews"].append(
                        {
                            "position": slot.position,
                            "candidates": pair,
                            "judgments": comparison,
                        }
                    )
                assessments.append(rows)
                proof["assessments"] = assessments
                save()
        finally:
            reranker.client.close()
        proposal = visual_storyboard_assignment(slots, materials, assessments)
        proof["proposal"] = proposal.model_dump(mode="json")
        save()
        return proposal
