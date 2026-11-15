"""Bounded second opinions for visually similar source clips."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from camcat.agent.storyboard import StoryboardSlot
    from camcat.services.narrative import NativeNarrativeClient


def source_motion_card(path: Path, name: str, *, width: int = 512) -> dict[str, Any]:
    """Sample real frames and reuse the visual service's LK/RANSAC implementation."""
    import cv2
    import numpy as np

    from services.local_models.camera_motion import background_motion

    capture = cv2.VideoCapture(str(path))
    try:
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if count < 2 or fps <= 0:
            raise ValueError("Source video has no measurable frame sequence")
        frames, times = [], []
        for index in np.unique(np.linspace(0, count - 1, min(count, 16)).round().astype(int)):
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = capture.read()
            if not ok:
                raise ValueError("Cannot read an actual storyboard source frame")
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            times.append(float(index / fps))
    finally:
        capture.release()
    profile = background_motion(np.stack(frames))
    pictures: list[dict[str, Any]] = []
    evidence = []
    for index in np.unique(np.linspace(0, len(frames) - 1, 5).round().astype(int)):
        frame = cv2.cvtColor(frames[index], cv2.COLOR_RGB2BGR)
        actual_width = min(width, frame.shape[1])
        frame = cv2.resize(
            frame, (actual_width, round(frame.shape[0] * actual_width / frame.shape[1]))
        )
        ok, jpeg = cv2.imencode(".jpg", frame)
        if not ok:
            raise ValueError("Cannot encode an actual storyboard source frame")
        evidence.append({"at": times[index], "sha256": hashlib.sha256(jpeg.tobytes()).hexdigest()})
        pictures.extend(
            [
                {"type": "text", "text": f"{name}: actual source frame at {times[index]:.3f}s"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64," + base64.b64encode(jpeg.tobytes()).decode()
                    },
                },
            ]
        )
    return {"profile": profile, "pictures": pictures, "frames": evidence}


def compare_candidates(
    client: NativeNarrativeClient,
    slot: StoryboardSlot,
    names: list[str],
    cards: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    judgments = []
    for order in (names, list(reversed(names))):
        content = [
            {
                "type": "text",
                "text": json.dumps(
                    {"storyboard_slot": slot.model_dump(exclude_none=True)}, ensure_ascii=False
                ),
            }
        ]
        for name in order:
            profile = cards[name]["profile"]
            compact = {
                k: profile[k]
                for k in ("scale_change", "valid_pairs", "total_pairs", "median_inlier_fraction")
            }
            compact["center_displacements"] = [
                [s["center_dx"], s["center_dy"], s["scale"]] for s in profile["steps"]
            ]
            content.append(
                {
                    "type": "text",
                    "text": json.dumps({"source": name, "measured_background_motion": compact}),
                }
            )
            content.extend(cards[name]["pictures"])
        judgments.append(
            client.complete(
                system=(
                    "Compare these two actual source clips against the exact storyboard. "
                    "Compare OPENING framing with shot_size: a later zoom out does not make a "
                    "close opening an extreme-wide opening. Medium-wide means knees-up; medium "
                    "means waist-up; medium-close-up means chest-up; wide shows the full body; "
                    "extreme-wide makes the person small within a large environment. Compare "
                    "initial facing explicitly using viewer-left/viewer-right: the direction "
                    "the face points, not where the subject stands in the frame. Track the "
                    "subject's initial posture and action over time. Distinguish zoom/pullback, "
                    "vertical boom/tilt, lateral camera motion and subject movement. The actual "
                    "peripheral LK/RANSAC measurements describe image motion, not physical camera "
                    "pose: scale_change above 1 means background features GREW; below 1 means "
                    "they SHRANK. Positive image dx means the background moved to image-right. "
                    "Use reliability and actual frames together. Prefer the clip matching more "
                    "defining details, weighted by their importance for this shot. Do not invent "
                    "unseen details or a movie identity. Candidate order carries no meaning."
                ),
                user=content,
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "preferred": {"type": "string", "enum": names},
                        "opening_comparison": {"type": "string", "maxLength": 400},
                        "motion_comparison": {"type": "string", "maxLength": 400},
                        "confidence": {"type": "string", "enum": ["clear", "weak", "uncertain"]},
                    },
                    "required": [
                        "preferred",
                        "opening_comparison",
                        "motion_comparison",
                        "confidence",
                    ],
                },
                max_tokens=1800,
                thinking=True,
                reasoning_tokens=512,
                temperature=0,
                presence_penalty=0,
            )
        )
    for judgment in judgments:
        judgment["ranking"] = [
            judgment["preferred"],
            *[n for n in names if n != judgment["preferred"]],
        ]
    return judgments


def ranked_assessments(names: list[str], judgments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fuse visual ranks without dropping candidates on uncertain first-pass labels."""
    if not judgments or any(
        len(j.get("ranking", [])) != len(names)
        or set(j["ranking"]) != set(names)
        or j["ranking"][0] != j.get("preferred")
        for j in judgments
    ):
        raise ValueError("Visual reviews must retain the exact candidate set and preferred first")
    return [
        {
            "material_id": name,
            "contradiction": False,
            "fit": sum(
                100 * (len(names) - j["ranking"].index(name)) / len(names) for j in judgments
            )
            / len(judgments),
            "reason": str(
                next(
                    (j["opening_comparison"] for j in judgments if j["preferred"] == name),
                    "Visual ranking of actual source frames",
                )
            )[:192],
        }
        for name in names
    ]


def video_assessments(names: list[str], scores: list[float]) -> list[dict[str, Any]]:
    if len(names) != len(scores) or any(not math.isfinite(s) or not 0 <= s <= 1 for s in scores):
        raise ValueError("Direct video reranker must return one finite probability per candidate")
    return [
        {
            "material_id": name,
            "fit": score * 100,
            "contradiction": False,
            "reason": "Official Qwen3-VL direct-video relevance",
        }
        for name, score in zip(names, scores, strict=True)
    ]


def apply_pairwise_review(
    rows: list[dict[str, Any]], names: list[str], judgments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Reverse a close pair only when both presentation orders agree."""
    output = copy.deepcopy(rows)
    if (
        len(names) != 2
        or len(set(names)) != 2
        or len(judgments) != 2
        or any(j.get("preferred") not in names for j in judgments)
        or any(j.get("confidence") not in {"clear", "weak", "uncertain"} for j in judgments)
        or judgments[0]["preferred"] != judgments[1]["preferred"]
    ):
        return output
    by_id = {r["material_id"]: r for r in output}
    if set(names) - by_id.keys() or any(by_id[n]["contradiction"] for n in names):
        return output
    winner = by_id[judgments[0]["preferred"]]
    loser = by_id[next(n for n in names if n != winner["material_id"])]
    high, low = max(winner["fit"], loser["fit"]), min(winner["fit"], loser["fit"])
    if high == low:
        high, low = min(100, high + 0.5), max(0, low - 0.5)
    winner["fit"], loser["fit"] = high, low
    label = (
        "Visual preference (uncertain): "
        if any(j["confidence"] == "uncertain" for j in judgments)
        else ""
    )
    winner["reason"] = (label + str(judgments[0].get("opening_comparison", winner["reason"])))[:192]
    return output
