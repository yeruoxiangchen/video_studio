from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from camcat.domain.localization import LocalizationCue
from camcat.schemas import JobResponse


class SavedVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_version: int = Field(ge=1)


class TranslateRequest(SavedVersionRequest):
    target_language: str = Field(default="en", pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
    source_track_id: str | None = Field(default=None, max_length=200)
    glossary: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_glossary(self) -> TranslateRequest:
        if len(self.glossary) > 50 or any(
            not key.strip() or not value.strip() or len(key) > 200 or len(value) > 200
            for key, value in self.glossary.items()
        ):
            raise ValueError("术语表最多 50 项，每项需要有效的原文和译文")
        return self


class DubRequest(SavedVersionRequest):
    voice: str | None = Field(default=None, max_length=200)
    allow_duration_rewrite: bool = False
    original_audio_volume: float = Field(default=0, ge=0, le=1)


class ApplyLocalizationRequest(SavedVersionRequest):
    pass


class ConfirmLocalizationReview(SavedVersionRequest):
    confirmed: Literal[True]


class SourceReviewHint(BaseModel):
    filename: str
    start_seconds: float
    end_seconds: float
    low_confidence_word_count: int
    words: list[str]


class DeliveryStatus(BaseModel):
    state: Literal[
        "needs_translation",
        "needs_dubbing",
        "needs_review",
        "ready_to_render",
        "rendering",
        "ready",
        "failed",
    ]
    blockers: list[str] = Field(default_factory=list)
    reviewed_version: int | None = None
    render_job: JobResponse | None = None
    package_download_url: str | None = None


class SubtitleTrackInfo(BaseModel):
    track_id: str
    name: str
    cue_count: int
    is_translation: bool


class LocalizationContext(BaseModel):
    base_version: int
    source_track_id: str | None
    target_track_id: str | None
    language: str | None
    tracks: list[SubtitleTrackInfo]
    cues: list[LocalizationCue]
    jobs: list[JobResponse]
    tts_configured: bool
    tts_voice: str | None
    tts_languages: list[str]
    audio_outdated: bool = False
    translation_outdated: bool = False
    source_cues: list[LocalizationCue] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    review_hints: list[SourceReviewHint] = Field(default_factory=list)
    delivery: DeliveryStatus | None = None
    expires_at: str | None = None
