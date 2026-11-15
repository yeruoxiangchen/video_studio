"""Interpret measured visible joints and robust image-plane background motion."""

from __future__ import annotations

import copy
import math
import statistics
from typing import Any


def visible_body_parts(
    points: list[list[float]], scores: list[float], width: int, height: int
) -> dict[str, bool]:
    return {
        name: any(
            scores[i] >= 0.5 and 1 < points[i][0] < width - 1 and 1 < points[i][1] < height - 1
            for i in indices
        )
        for name, indices in {
            "ankles": (15, 16),
            "knees": (13, 14),
            "hips": (11, 12),
            "shoulders": (5, 6),
        }.items()
    }


def framing_from_parts(frames: list[dict[str, bool]]) -> str | None:
    if len(frames) < 3:
        return None
    for part, shot in (
        ("ankles", "wide"),
        ("knees", "medium wide"),
        ("hips", "medium"),
        ("shoulders", "medium close-up"),
    ):
        if sum(bool(frame.get(part)) for frame in frames) / len(frames) >= 0.6:
            return shot
    return None


def camera_lateral_direction(measurements: dict[str, Any]) -> str:
    width = measurements.get("working_width", 384)
    height = measurements.get("working_height", 216)
    steps = [s for s in measurements.get("steps", []) if s.get("inlier_fraction", 0) >= 0.7]
    if len(steps) < 8:
        return "uncertain"
    dx = [float(s.get("center_dx", 0)) for s in steps]
    dy = [float(s.get("center_dy", 0)) for s in steps]
    horizontal, vertical = statistics.median(dx), statistics.median(dy)
    zoom = statistics.median(abs(math.log(max(1e-9, float(s["scale"])))) for s in steps)
    if (
        not math.isfinite(horizontal)
        or abs(horizontal) * width < 1
        or abs(horizontal) * width < abs(vertical) * height
        or abs(horizontal) <= 0.5 * zoom
        or max(sum(value > 0 for value in dx), sum(value < 0 for value in dx)) / len(dx) < 0.9
    ):
        return "uncertain"
    # Under the static-background assumption, consistent image drift opposes
    # apparent camera motion. This does not estimate 3D camera translation.
    return "left" if horizontal > 0 else "right"


def refine_source_semantics(
    model_observations: dict[str, Any], geometry: dict[str, Any]
) -> dict[str, Any]:
    result = copy.deepcopy(model_observations)
    if geometry.get("shot_size"):
        result["shot_size"] = geometry["shot_size"]
    if geometry.get("camera_lateral_direction") in {"left", "right"}:
        result["camera_motion"] = "track " + geometry["camera_lateral_direction"]
    return result
