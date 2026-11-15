"""Translation output uses the same typed schema at the provider boundary."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from camcat.domain.localization import LocalizationCue


class TranslatedCue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=10_000)


class TranslationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    translations: list[TranslatedCue] = Field(min_length=1, max_length=100)


def translation_response_schema(cues: list[LocalizationCue]) -> dict[str, Any]:
    schema = TranslationResponse.model_json_schema()
    schema["$defs"]["TranslatedCue"]["properties"]["id"]["enum"] = [cue.cue_id for cue in cues]
    schema["properties"]["translations"].update(minItems=len(cues), maxItems=len(cues))
    return schema
