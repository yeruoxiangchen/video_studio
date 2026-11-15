"""Bound planning context while preserving every original source and its measured range."""

from __future__ import annotations

import math
from typing import Any

from camcat.domain.project import EditingProjectV2


def source_duration_limits(
    project: EditingProjectV2, materials: list[dict[str, Any]]
) -> dict[str, int]:
    limits: dict[str, int] = {}
    for item in materials:
        if item.get("source_duration_us"):
            source_id = str(item["source_id"])
            limits[source_id] = max(
                limits.get(source_id, 0),
                int(item["source_start_us"]) + int(item["source_duration_us"]),
            )
    metadata = {s.source_id: s.metadata for s in project.sources}
    for media in project.metadata.get("source_media", []):
        metadata[str(media["media_id"])] = media
    for source_id, detail in metadata.items():
        duration = detail.get("duration_us")
        seconds = detail.get("duration_seconds")
        if isinstance(duration, int) and duration > 0:
            limits[source_id] = duration
        elif isinstance(seconds, (float, int)) and math.isfinite(seconds) and seconds > 0:
            limits[source_id] = round(seconds * 1_000_000)
    return limits


def planning_snapshot(project: EditingProjectV2) -> dict[str, Any]:
    snapshot = project.model_dump(mode="json", by_alias=True, exclude={"metadata"})
    snapshot["sources"] = [{"source_id": s.source_id, "origin": s.origin} for s in project.sources]
    snapshot["goal"] = project.goal[:600]
    return snapshot


def prompt_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove schema annotations, preserving property names and all validation keywords."""

    def compact(value: Any) -> Any:
        if isinstance(value, list):
            return [compact(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {
            key: (
                {name: compact(child) for name, child in item.items()}
                if key in {"properties", "$defs"}
                else compact(item)
            )
            for key, item in value.items()
            if key not in {"title", "description"}
        }

    return dict(compact(schema))


def compact_materials(
    materials: list[dict[str, Any]], project: EditingProjectV2
) -> list[dict[str, Any]]:
    media = {s.source_id: s.metadata for s in project.sources if s.origin == "user_upload"}
    for item in project.metadata.get("source_media", []):
        media[str(item["media_id"])] = item
    grouped: dict[str, list[dict[str, Any]]] = {}
    output: list[dict[str, Any]] = []
    for item in materials:
        if item["origin"] == "user_upload":
            grouped.setdefault(str(item["source_id"]), []).append(item)
        else:
            output.append({**item, "description": str(item.get("description", ""))[:400]})
    # Retain files whose shots were deduplicated: file selection and shot deduplication
    # are different decisions, and a request to reorder every upload needs every file.
    for source_id, detail in media.items():
        if isinstance(detail.get("duration_seconds"), (float, int)):
            grouped.setdefault(source_id, [])
    originals: list[dict[str, Any]] = []
    limits = source_duration_limits(project, materials)
    for source_id, windows in grouped.items():
        detail = media.get(source_id, {})
        if not detail.get("duration_seconds"):
            originals.extend(windows)
            continue
        semantics = detail.get("visual_semantics") or {}
        description = "; ".join(
            str(value)
            for value in (
                semantics.get("description", ""),
                semantics.get("scene", ""),
                semantics.get("composition", ""),
            )
            if value
        )[:1120]
        fallback_description = str(windows[0].get("description", ""))[:400] if windows else ""
        item = {
            "source_id": source_id,
            "origin": "user_upload",
            "media_type": "video",
            "filename": detail.get("filename", ""),
            "source_start_us": 0,
            "source_duration_us": limits[source_id],
            "description": description or fallback_description,
            "quality_score": max(
                (float(w.get("quality_score", 0.5)) for w in windows), default=0.5
            ),
        }
        if detail.get("visual_evidence"):
            item["visual_evidence"] = detail["visual_evidence"]
        if semantics.get("environment"):
            item["environment"] = semantics["environment"]
        shot = {
            key: semantics[key]
            for key in (
                "shot_size",
                "camera_angle",
                "camera_motion",
                "subject_view",
                "subject_motion",
            )
            if semantics.get(key) and semantics[key] != "uncertain"
        }
        if shot:
            item["shot"] = shot
        if semantics.get("temporal_evidence"):
            item["temporal_evidence"] = semantics["temporal_evidence"][:6]
        for key in ("people", "actions"):
            if semantics.get(key):
                item[key] = semantics[key][:6]
        item["analyzed_windows"] = [
            {
                "start_us": w["source_start_us"],
                "duration_us": w["source_duration_us"],
                **({"transcript": str(w["transcript"])[:160]} if w.get("transcript") else {}),
            }
            for w in windows
        ]
        originals.append(item)
    return originals + output
