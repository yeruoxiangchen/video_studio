"""The agent proposes native delta commands; source registration and canvas stay server-owned."""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from camcat.domain.commands import (
    AddAudioSegment,
    AddTrack,
    InsertSegment,
    MoveSegment,
    RemoveAudioSegment,
    RemoveSegment,
    RemoveTrack,
    RemoveTransition,
    RenameTrack,
    ReplaceSegmentMedia,
    SetSegmentTransform,
    SetTransition,
    SplitSegment,
    TrimSegment,
    UpdateAudioSegment,
    UpdateGoal,
    UpdateTextSegment,
    UpdateTitle,
)
from camcat.domain.project import AudioTrack, EditingProjectV2, TextTrack, VideoTrack

PlanningCommand = Annotated[
    AddTrack
    | RemoveTrack
    | RenameTrack
    | InsertSegment
    | MoveSegment
    | RemoveSegment
    | TrimSegment
    | SplitSegment
    | ReplaceSegmentMedia
    | SetSegmentTransform
    | UpdateTextSegment
    | AddAudioSegment
    | UpdateAudioSegment
    | RemoveAudioSegment
    | SetTransition
    | RemoveTransition
    | UpdateTitle
    | UpdateGoal,
    Field(discriminator="type"),
]
InitialPlanningCommand = Annotated[
    AddTrack | InsertSegment | AddAudioSegment | SetTransition | UpdateTitle | UpdateGoal,
    Field(discriminator="type"),
]
RefinementPlanningCommand = Annotated[
    TrimSegment
    | SetSegmentTransform
    | UpdateTextSegment
    | UpdateAudioSegment
    | RemoveAudioSegment
    | RemoveTransition
    | UpdateTitle
    | UpdateGoal,
    Field(discriminator="type"),
]


class CommandProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    commands: tuple[PlanningCommand, ...] = Field(min_length=1, max_length=100)

    @field_validator("commands", mode="before")
    @classmethod
    def derive_video_durations(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        commands = deepcopy(value)
        for command in commands:
            if not isinstance(command, dict):
                continue
            if command.get("type") == "insert_segment":
                segments = [command.get("segment")]
            elif command.get("type") == "add_track" and isinstance(command.get("track"), dict):
                segments = command["track"].get("segments", [])
            else:
                continue
            if not isinstance(segments, list):
                continue
            for segment in segments:
                if not isinstance(segment, dict) or segment.get("type") != "video":
                    continue
                source_duration = segment.get("source_duration_us")
                speed = segment.get("speed", 1.0)
                if (
                    isinstance(source_duration, int)
                    and source_duration > 0
                    and isinstance(speed, (int, float))
                    and 0.1 <= speed <= 8.0
                ):
                    segment["timeline_duration_us"] = round(source_duration / speed)
        return commands


class InitialCommandProposal(CommandProposal):
    commands: tuple[InitialPlanningCommand, ...] = Field(min_length=1, max_length=100)


class RefinementCommandProposal(CommandProposal):
    """Repair a revision while retaining the uploaded video already in the project."""

    commands: tuple[RefinementPlanningCommand, ...] = Field(min_length=1, max_length=100)


class RevisionCommandProposal(RefinementCommandProposal):
    """Prefer existing-segment edits; request the full contract for structural work."""

    commands: tuple[RefinementPlanningCommand, ...] = Field(max_length=24)
    requires_full_plan: bool = Field(
        default=False,
        description=("True with empty commands for structural operations or more than 24 changes."),
    )

    @model_validator(mode="after")
    def validate_route(self) -> RevisionCommandProposal:
        if self.requires_full_plan == bool(self.commands):
            raise ValueError("return existing edits, or request the full plan with empty commands")
        return self


class PrimaryClipProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1)
    source_start_us: int = Field(ge=0)
    source_duration_us: int = Field(gt=0)

    @classmethod
    def schema_for_sources(cls, source_ids: list[str]) -> dict[str, Any]:
        schema = cls.model_json_schema()
        schema["properties"]["source_id"]["enum"] = source_ids
        return schema


def command_proposal_schema(
    model: type[CommandProposal] = CommandProposal, *, allow_video_transform: bool = True
) -> dict[str, Any]:
    schema = model.model_json_schema()
    video = schema.get("$defs", {}).get("VideoSegment")
    if video:
        del video["properties"]["timeline_duration_us"]
        video["required"].remove("timeline_duration_us")

    def require_discriminators(value: Any) -> None:
        if isinstance(value, dict):
            if "const" in value.get("properties", {}).get("type", {}):
                value["required"] = sorted(set(value.get("required", [])) | {"type"})
            for child in value.values():
                require_discriminators(child)
        elif isinstance(value, list):
            for child in value:
                require_discriminators(child)

    require_discriminators(schema)
    if not allow_video_transform:
        if video:
            video["properties"].pop("transform", None)
        choices = schema["properties"]["commands"]["items"]
        reference = choices["discriminator"]["mapping"].pop("set_segment_transform", None)
        if reference:
            choices["oneOf"] = [item for item in choices["oneOf"] if item.get("$ref") != reference]
    return schema


def refinement_proposal_schema(
    project: EditingProjectV2,
    model: type[RefinementCommandProposal] = RefinementCommandProposal,
    *,
    allow_video_transform: bool = True,
) -> dict[str, Any]:
    """Constrain repair references to the segments present in the original snapshot."""
    schema = command_proposal_schema(model, allow_video_transform=allow_video_transform)
    video = sorted(
        segment.segment_id
        for track in project.timeline.tracks
        if isinstance(track, VideoTrack)
        for segment in track.segments
    )
    captions = sorted(
        segment.segment_id
        for track in project.timeline.tracks
        if isinstance(track, TextTrack)
        for segment in track.segments
    )
    audio = sorted(
        segment.segment_id
        for track in project.timeline.tracks
        if isinstance(track, AudioTrack)
        for segment in track.segments
    )
    references = {
        "TrimSegment": ("segment_id", video),
        "SetSegmentTransform": ("segment_id", video),
        "UpdateTextSegment": ("segment_id", captions),
        "UpdateAudioSegment": ("segment_id", audio),
        "RemoveAudioSegment": ("segment_id", audio),
        "RemoveTransition": (
            "transition_id",
            sorted(edge.transition_id for edge in project.timeline.transitions),
        ),
    }
    choices = schema["properties"]["commands"]["items"]
    mapping = choices["discriminator"]["mapping"]
    for command, reference in list(mapping.items()):
        definition = reference.rsplit("/", 1)[-1]
        if definition not in references:
            continue
        field, identifiers = references[definition]
        if identifiers:
            schema["$defs"][definition]["properties"][field]["enum"] = identifiers
        else:
            del mapping[command]
            choices["oneOf"] = [item for item in choices["oneOf"] if item.get("$ref") != reference]
    return schema
