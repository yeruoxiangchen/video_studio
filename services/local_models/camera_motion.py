"""Measure peripheral image motion with OpenCV LK flow and RANSAC.

These are image measurements, not a physical camera-pose estimate. Low coverage,
independent moving objects and scene cuts limit what a model can conclude.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any


def background_motion(frames: Any) -> dict[str, Any]:
    import cv2
    import numpy as np

    raw = frames.detach().cpu().numpy() if hasattr(frames, "detach") else np.asarray(frames)
    if raw.ndim != 4:
        raise ValueError("Expected an actual video frame sequence")
    if raw.shape[1] == 3:
        raw = np.moveaxis(raw, 1, -1)
    indices = np.unique(np.linspace(0, len(raw) - 1, min(16, len(raw))).round().astype(int))
    gray = []
    for index in indices:
        frame = np.clip(raw[index], 0, 255).astype(np.uint8)
        height, width = frame.shape[:2]
        resized = cv2.resize(frame, (384, max(32, round(height * 384 / width))))
        gray.append(cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY))
    steps = []
    cv2.setNumThreads(2)
    for previous, current in zip(gray[:-1], gray[1:], strict=True):
        height, width = previous.shape
        mask = np.full(previous.shape, 255, dtype=np.uint8)
        # Do not treat a centered walking actor as the background.
        mask[:, width // 4 : 3 * width // 4] = 0
        points = cv2.goodFeaturesToTrack(
            previous, maxCorners=400, qualityLevel=0.01, minDistance=7, mask=mask
        )
        if points is None or len(points) < 8:
            continue
        tracked, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None)
        if tracked is None:
            continue
        backward, reverse_status, _ = cv2.calcOpticalFlowPyrLK(current, previous, tracked, None)
        if backward is None:
            continue
        consistent = (status.ravel() == 1) & (reverse_status.ravel() == 1)
        consistent &= np.linalg.norm(backward - points, axis=2).ravel() < 1
        if np.count_nonzero(consistent) < 8:
            continue
        transform, inliers = cv2.estimateAffinePartial2D(
            points[consistent],
            tracked[consistent],
            method=cv2.RANSAC,
            ransacReprojThreshold=3,
            maxIters=2000,
            confidence=0.99,
            refineIters=10,
        )
        if transform is None or inliers is None:
            continue
        scale = math.hypot(float(transform[0, 0]), float(transform[0, 1]))
        if not math.isfinite(scale) or scale <= 0:
            continue
        center = np.array([width / 2, height / 2])
        center_flow = transform[:, :2] @ center + transform[:, 2] - center
        steps.append(
            {
                "scale": scale,
                "inlier_fraction": float(inliers.mean()),
                "tracked_points": int(np.count_nonzero(consistent)),
                "image_dx": float(transform[0, 2]) / width,
                "image_dy": float(transform[1, 2]) / height,
                "center_dx": float(center_flow[0]) / width,
                "center_dy": float(center_flow[1]) / height,
            }
        )
    return {
        "method": "OpenCV peripheral Shi-Tomasi + forward/backward LK + RANSAC partial affine",
        "working_width": 384,
        "working_height": int(gray[0].shape[0]) if gray else 0,
        "sampled_frames": len(gray),
        "valid_pairs": len(steps),
        "total_pairs": max(0, len(gray) - 1),
        "scale_change": round(math.prod(step["scale"] for step in steps), 5) if steps else None,
        "median_inlier_fraction": round(float(np.median([s["inlier_fraction"] for s in steps])), 4)
        if steps
        else None,
        "steps": [
            {
                key: round(value, 5) if isinstance(value, float) else value
                for key, value in step.items()
            }
            for step in steps
        ],
    }


def with_background_evidence(
    messages: list[dict[str, Any]], measurements: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    output = copy.deepcopy(messages)
    evidence = {
        "measured_background_motion": measurements,
        "interpretation": (
            "These measurements come from the actual sampled video frames, excluding the "
            "central subject. A scale above one means peripheral background features grew; "
            "below one means they shrank. Consistent shrinking and newly revealed background "
            "support outward apparent camera motion, not a static camera or push-in. "
            "Subject approach alone is not camera approach. Image translation is not camera "
            "translation. Coverage and RANSAC inlier fractions describe reliability; cuts and "
            "independent moving objects can weaken the estimate. Use the visible video with "
            "these measurements, and mark unsupported physical camera directions uncertain."
        ),
    }
    part = {"type": "text", "text": json.dumps(evidence, ensure_ascii=False)}
    if output[-1]["role"] == "user" and isinstance(output[-1]["content"], list):
        output[-1]["content"].append(part)
    else:
        output.append({"role": "user", "content": [part]})
    return output
