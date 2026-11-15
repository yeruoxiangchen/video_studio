"""Bounded model-selected tools. Editing keeps its existing LangGraph route."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from camcat.domain.project import EditingProjectV2
from camcat.localization.schemas import DubRequest, TranslateRequest
from camcat.schemas import EditingSessionResponse, JobResponse
from camcat.services.providers import QwenChatClient


class AgentDispatchResponse(BaseModel):
    action: Literal["edit", "translate", "dub", "translate_and_dub", "clarify"]
    message: str
    session: EditingSessionResponse
    job: JobResponse | None = None


class LocalizationAction(TranslateRequest):
    model_config = ConfigDict(extra="forbid")
    base_version: int = Field(default=1, ge=1)
    action: Literal["edit", "translate", "dub", "translate_and_dub", "clarify"]
    allow_duration_rewrite: bool = False
    original_audio_volume: float = Field(default=0, ge=0, le=1)
    message: str = Field(default="", max_length=500)


def may_request_localization(instruction: str) -> bool:
    return bool(
        re.search(
            r"翻译|译制|配音|译成|译为|译文|translate|translation|dubb?(?:ing)?|voice.?over",
            instruction,
            re.I,
        )
    )


def resolve_action(
    instruction: str, project: EditingProjectV2, llm: QwenChatClient
) -> LocalizationAction:
    if not may_request_localization(instruction):
        return LocalizationAction(action="edit")
    metadata = project.metadata.get("localization", {})
    result = llm.json_completion(
        system=(
            "You select one video workstation tool from the user's CURRENT request. "
            "Return JSON only. "
            "edit: trimming, selecting shots, adding subtitles, or quoted dialogue about "
            "translation. "
            "translate: translate subtitles only. dub: voice the existing translated "
            "subtitles. "
            "translate_and_dub: explicitly requested translation AND voiceover, or "
            "explicitly requested dubbing into a different language. "
            "clarify: question, negation, unsupported request, or insufficient target "
            "language for translation. "
            "Do not execute an action the user negates or only asks about. '翻译成英文' is "
            "translate; "
            "'翻译成英文并配音' is translate_and_dub; '给译文配音' is dub; "
            "'剪掉提到配音的片段' is edit. "
            "target_language uses ISO codes en/zh/ja/fr/es, never a language guessed from"
            " the user's input language. "
            "For dub use current_language. For translate without a named language use "
            "current_language if present; otherwise clarify. "
            "Only include glossary mappings explicitly supplied by the user, otherwise {}. "
            "allow_duration_rewrite is true ONLY when explicitly allowed. "
            "original_audio_volume defaults to 0, "
            "0.25 for explicit keep a quiet original. source_track_id is null. "
            "base_version is 1. "
            "message is a concise Chinese description of the selected action. Never "
            "approve review or render."
        ),
        user=json.dumps(
            {
                "instruction": instruction,
                "current_language": metadata.get("language")
                if isinstance(metadata, dict)
                else None,
            },
            ensure_ascii=False,
        ),
        response_schema=LocalizationAction.model_json_schema(),
        temperature=0,
    )
    return LocalizationAction.model_validate(result)


def dubbing_request(action: LocalizationAction, *, version: int) -> DubRequest:
    return DubRequest(
        base_version=version,
        allow_duration_rewrite=action.allow_duration_rewrite,
        original_audio_volume=action.original_audio_volume,
    )
