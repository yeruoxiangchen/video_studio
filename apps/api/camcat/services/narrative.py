"""Real native multimodal completions for original-source narrative reconstruction."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from camcat.agent.errors import AgentInputError
from camcat.agent.narrative import (
    BoundaryCaptions,
    NarrativeFacts,
    expand_block_order,
    narrative_brief,
    narrative_materials,
    narrative_sample_times,
    subtitle_blocks,
)
from camcat.agent.narrative_prompts import (
    OCR_SYSTEM,
    SOURCE_EVENTS_SYSTEM,
    block_instructions,
)
from camcat.agent.sequence import (
    SourceSequenceClip,
    SourceSequenceProposal,
    material_aliases,
    source_filenames,
)
from camcat.agent.storyboard import StoryboardSlot
from camcat.config import Settings
from camcat.domain.project import EditingProjectV2
from camcat.media.ffmpeg import extract_analysis_frame, probe
from camcat.services.object_store import ObjectStore
from camcat.services.providers import (
    ProviderError,
    ProviderOutputError,
    _ProviderClient,
    parse_structured_content,
)


class NativeNarrativeClient(_ProviderClient):
    def __init__(self, settings: Settings) -> None:
        if not settings.narrative_base_url or not settings.narrative_model:
            raise ValueError("Configure the real narrative provider before using it")
        super().__init__(
            base_url=settings.narrative_base_url,
            api_key=settings.narrative_api_key.get_secret_value(),
            settings=settings,
        )
        self.model = settings.narrative_model

    def healthcheck(self) -> None:
        super().healthcheck()
        models = self.request("GET", "/v1/models")
        if not any(model.get("id") == self.model for model in models.get("data", [])):
            raise ProviderError("The configured narrative model is not loaded", retryable=False)

    def complete(
        self,
        *,
        system: str,
        user: str | list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int,
        thinking: bool,
        reasoning_tokens: int = 4096,
        temperature: float | None = None,
        presence_penalty: float | None = None,
    ) -> dict[str, Any]:
        payload = self.request(
            "POST",
            "/v1/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "camcat_narrative", "strict": True, "schema": schema},
                },
                "temperature": temperature if temperature is not None else 1.0 if thinking else 0.7,
                "top_p": 0.95 if thinking else 0.8,
                "top_k": 20,
                "presence_penalty": (
                    presence_penalty if presence_penalty is not None else 0 if thinking else 1.5
                ),
                "seed": 42,
                "max_tokens": max_tokens,
                "reasoning_budget_tokens": reasoning_tokens if thinking else 0,
                "chat_template_kwargs": {"enable_thinking": thinking},
            },
        )
        try:
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ProviderOutputError("Native narrative completion exceeded its token budget")
            return parse_structured_content(choice["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderOutputError(
                "Native narrative model returned invalid structured JSON"
            ) from error


Frame = tuple[Path, float]


def _pictures(frames: list[Frame], label: str = "Frame") -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for path, at in frames:
        parts.extend(
            [
                {"type": "text", "text": f"{label} at {at:.3f}s"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64,"
                        + base64.b64encode(path.read_bytes()).decode()
                    },
                },
            ]
        )
    return parts


class NativeNarrativePlanner:
    """Bounded observation, draft, visual review and native full-source ordering."""

    def __init__(self, settings: Settings, *, object_store: ObjectStore) -> None:
        self.settings = settings
        self.object_store = object_store
        self.client = NativeNarrativeClient(settings)

    def healthcheck(self) -> None:
        self.client.healthcheck()

    def plan_storyboard(
        self,
        *,
        project: EditingProjectV2,
        slots: list[StoryboardSlot],
        originals: list[dict[str, Any]],
    ) -> SourceSequenceProposal:
        from camcat.services.storyboard import review_storyboard

        return review_storyboard(
            self.client,
            self.settings,
            self.object_store,
            project=project,
            slots=slots,
            originals=originals,
        )

    def plan(
        self,
        *,
        project: EditingProjectV2,
        instruction: str,
        originals: list[dict[str, Any]],
        count: int | None,
        use_all_sources: bool,
    ) -> SourceSequenceProposal:
        try:
            return self._plan(
                project=project,
                instruction=instruction,
                originals=originals,
                count=count,
                use_all_sources=use_all_sources,
            )
        except AgentInputError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderOutputError(
                "Narrative evidence did not form a valid source order"
            ) from error

    def _plan(
        self,
        *,
        project: EditingProjectV2,
        instruction: str,
        originals: list[dict[str, Any]],
        count: int | None,
        use_all_sources: bool,
    ) -> SourceSequenceProposal:
        ids = {str(item["source_id"]) for item in originals}
        if not use_all_sources or count not in {None, len(ids)}:
            raise AgentInputError(
                "Narrative ordering requires all original sources exactly once",
                code="narrative_source_count",
            )
        if len(ids) > 16:
            raise AgentInputError(
                "本地叙事排序单次最多处理 16 段原片，请将素材分组。", code="narrative_source_limit"
            )
        aliases = material_aliases(ids, source_filenames(project))
        registry = {source.source_id: source for source in project.sources}
        media_order = {
            str(media["media_id"]): i
            for i, media in enumerate(project.metadata.get("source_media", []))
        }
        names = sorted(aliases, key=lambda name: (media_order.get(aliases[name], len(ids)), name))
        root = Path(self.settings.runtime_dir)
        temporary = root / "narrative-tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        evidence = root / "planning-evidence" / uuid4().hex
        evidence.mkdir(parents=True, mode=0o700)
        brief = narrative_brief(instruction)
        facts: dict[str, NarrativeFacts] = {}
        frames: dict[str, list[Frame]] = {}
        proof: dict[str, Any] = {
            "project_id": project.project_id,
            "model": self.client.model,
            "manual_facts": False,
            "scope": "actual uploaded source frames and visible dialogue",
            "sources": {},
        }

        def save(name: str, value: Any) -> None:
            (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

        with TemporaryDirectory(dir=temporary, prefix="sources-") as work:
            for index, name in enumerate(names):
                source = Path(work) / f"source-{index:03}.mp4"
                source_id = aliases[name]
                self.object_store.download_file(registry[source_id].storage_key, source)
                metadata = probe(source)
                duration = metadata.video_duration or metadata.duration
                sampled = []
                for j, at in enumerate(narrative_sample_times(duration)):
                    image = Path(work) / f"frames-{index:03}" / f"{j:02}.jpg"
                    extract_analysis_frame(source, image, at=at)
                    sampled.append((image, at))
                frames[name] = sampled
                content = [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"source": name, "user_story_context": brief},
                            ensure_ascii=False,
                        ),
                    },
                    *_pictures(sampled, "Actual frame"),
                ]
                source_schema = NarrativeFacts.model_json_schema()
                source_schema["required"] = list(source_schema["properties"])
                raw = self.client.complete(
                    system=SOURCE_EVENTS_SYSTEM,
                    user=content,
                    schema=source_schema,
                    max_tokens=1900,
                    thinking=False,
                    temperature=0,
                    presence_penalty=0,
                )
                facts[name] = NarrativeFacts.model_validate(raw)
                proof["sources"][name] = {
                    "source_id": source_id,
                    "duration_seconds": duration,
                    "facts": raw,
                    "frames": [
                        {"at": at, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for path, at in sampled
                    ],
                }
                save("observations.json", proof)

            captions = self._captions(frames)
            save(
                "boundary-captions.json",
                {name: value.model_dump() for name, value in captions.items()},
            )
            blocks = subtitle_blocks(set(names), captions)
            sequence = self._blocks(brief, facts, captions, blocks, [], audit=evidence)
            save("initial-selection.json", {"source_aliases": sequence})
            from camcat.services.narrative_review import review_narrative

            sequence = review_narrative(
                self.client,
                brief=brief,
                facts=facts,
                captions=captions,
                frames=frames,
                chains=blocks,
                draft=sequence,
                audit=evidence,
            )
            save(
                "selection.json",
                {
                    "source_aliases": sequence,
                    "actual_source_ids": [aliases[name] for name in sequence],
                },
            )
            return SourceSequenceProposal(
                clips=tuple(
                    SourceSequenceClip(
                        material_id=name,
                        reason=("Observed opening: " + facts[name].opening_state)[:192],
                    )
                    for name in sequence
                )
            )

    def _captions(self, frames: dict[str, list[Frame]]) -> dict[str, BoundaryCaptions]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Transcribe ONLY burned-in dialogue subtitles from each opening and closing "
                    "video frame. Ignore in-scene signs, logos, and writing on props. Empty "
                    "string if no subtitle is visible; never guess missing words."
                ),
            }
        ]
        for name, samples in frames.items():
            for label, frame in (("opening", samples[0]), ("closing", samples[-1])):
                content.append(
                    {
                        "type": "text",
                        "text": f"{name} {label} frame. Copy its exact visible dialogue subtitle.",
                    }
                )
                content.extend(_pictures([frame]))
        row = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "source": {"type": "string", "enum": list(frames)},
                "opening_text": {"type": "string", "maxLength": 256},
                "closing_text": {"type": "string", "maxLength": 256},
            },
            "required": ["source", "opening_text", "closing_text"],
        }
        raw = self.client.complete(
            system=OCR_SYSTEM,
            user=content,
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sources": {
                        "type": "array",
                        "minItems": len(frames),
                        "maxItems": len(frames),
                        "items": row,
                    }
                },
                "required": ["sources"],
            },
            max_tokens=3072,
            thinking=False,
            temperature=0,
            presence_penalty=0,
        )
        output = {
            row["source"]: BoundaryCaptions.model_validate(
                {"opening_text": row["opening_text"], "closing_text": row["closing_text"]}
            )
            for row in raw["sources"]
        }
        if set(output) != set(frames) or len(raw["sources"]) != len(frames):
            raise ProviderOutputError("Caption OCR must retain every original source exactly once")
        return output

    def _blocks(
        self,
        brief: str,
        facts: dict[str, NarrativeFacts],
        captions: dict[str, BoundaryCaptions],
        chains: tuple[tuple[str, ...], ...],
        comparisons: list[dict[str, Any]],
        *,
        audit: Path | None = None,
    ) -> list[str]:
        _, aliases = narrative_materials(facts)
        reverse = {name: alias for alias, name in aliases.items()}
        blocks: dict[str, tuple[str, ...]] = {}
        cards: list[dict[str, Any]] = []
        for chain in chains:
            block_id = "block_" + hashlib.sha256("|".join(chain).encode()).hexdigest()[:10]
            blocks[block_id] = chain
            sources = []
            for name in chain:
                sources.append(
                    {
                        "material_id": reverse[name],
                        "facts": facts[name].model_dump(),
                        "observed_actor_states": [],
                        "visible_opening_subtitle": captions[name].opening_text,
                        "visible_closing_subtitle": captions[name].closing_text,
                    }
                )
            cards.append({"block_id": block_id, "sources_in_fixed_order": sources})
        payload = {
            "instruction": brief,
            "use_all_sources": True,
            "blocks": sorted(cards, key=lambda card: card["block_id"]),
            "confirmed_draft_corrections": [],
            "fixed_block_reason": (
                "Each multi-source block joins the same independently OCR-transcribed dialogue "
                "subtitle at one source ending and the next source opening. The identical "
                "nontrivial caption has exactly one opening and one closing occurrence among "
                "all supplied source boundaries."
            ),
        }
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "blocks": {
                    "type": "array",
                    "minItems": len(blocks),
                    "maxItems": len(blocks),
                    "items": {"type": "string", "enum": list(blocks)},
                }
            },
            "required": ["blocks"],
        }
        for attempt in range(2):
            raw = self.client.complete(
                system=block_instructions(brief),
                user=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                schema=schema,
                max_tokens=4608,
                thinking=True,
                temperature=0,
                presence_penalty=0,
            )
            if audit is not None:
                (audit / f"block-proposal-{attempt + 1}.json").write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
                )
            try:
                return expand_block_order(raw["blocks"], blocks, [])
            except (KeyError, TypeError, ValueError) as error:
                if attempt:
                    raise ProviderOutputError(
                        "Narrative order must include every observed source block exactly once"
                    ) from error
                payload["invalid_previous_proposal"] = raw
                payload["formal_validation_feedback"] = str(error)
        raise AssertionError("unreachable bounded narrative planning loop")
