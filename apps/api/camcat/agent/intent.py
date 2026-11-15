"""Typed requirement extraction; measured media and project state remain server-owned."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from camcat.editing.requirements import DeliveryConstraints


class RequirementIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    search_query: str = Field(max_length=1024)
    target_duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    style: str = Field(default="", max_length=256)
    event_type: str | None = Field(default=None, max_length=128)
    tags: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=20)
    platform: str = Field(default="", max_length=128)
    story_arc: str = Field(default="", max_length=1024)
    pacing: str = Field(default="", max_length=128)
    operation_scope: Literal["structural", "refine", "unknown"] = "unknown"
    planning_mode: Literal["commands", "source_sequence"] = "commands"
    caption_language: str | None = Field(
        default=None, pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$"
    )
    caption_operation: Literal["keep", "rewrite", "shorten", "translate"] = "keep"
    video_transform_requested: bool = False
    response_summary: str = Field(default="", max_length=1024)
    delivery_constraints: DeliveryConstraints = Field(default_factory=DeliveryConstraints)
