"""Bounded CPU inference using upstream RTMLib/RTMPose and OpenCV optical flow."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .source_geometry import camera_lateral_direction, framing_from_parts, visible_body_parts


def specification() -> dict[str, Any]:
    return dict(
        json.loads((Path(__file__).resolve().parents[2] / "infra/geometry.lock.json").read_text())
    )


def verify_models(root: Path, spec: dict[str, Any]) -> None:
    directory = root / spec["directory"]
    for record in spec["models"].values():
        path = directory / record["file"]
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            raise ValueError("Prepare the pinned CPU geometry models before visual startup")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("CPU geometry model SHA256 mismatch")


def measure(video: Path, root: Path) -> dict[str, Any]:
    # This child owns its CPU budget; it never creates a CUDA session.
    if hasattr(os, "sched_getaffinity"):
        allowed = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, allowed[-2:])
    import cv2
    import numpy as np
    import onnxruntime as ort
    from rtmlib import Body

    from .camera_motion import background_motion

    spec = specification()
    verify_models(root, spec)
    directory = root / spec["directory"]
    cv2.setNumThreads(2)
    with contextlib.redirect_stdout(sys.stderr):
        model = Body(
            det=str(directory / spec["models"]["detector"]["file"]),
            det_input_size=(416, 416),
            pose=str(directory / spec["models"]["pose"]["file"]),
            pose_input_size=(192, 256),
            backend="onnxruntime",
            device="cpu",
            to_openpose=False,
        )
    for tool in (model.det_model, model.pose_model):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        tool.session = ort.InferenceSession(
            tool.onnx_model, sess_options=options, providers=["CPUExecutionProvider"]
        )
    capture = cv2.VideoCapture(str(video))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    if count < 1 or fps <= 0:
        capture.release()
        raise ValueError("Actual source video has no decodable frames")
    indices = np.unique(np.linspace(0, max(0, count - 2), min(count, 16)).round().astype(int))
    pose_indices = set(np.linspace(0, len(indices) - 1, min(5, len(indices))).round().astype(int))
    frames = []
    samples: list[dict[str, Any]] = []
    try:
        for index, at in enumerate(indices):
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(at))
            ok, image = capture.read()
            if not ok:
                raise ValueError("Actual source frame could not be decoded")
            height, width = image.shape[:2]
            frames.append(
                cv2.cvtColor(
                    cv2.resize(image, (384, round(height * 384 / width))), cv2.COLOR_BGR2RGB
                )
            )
            if index not in pose_indices:
                continue
            boxes = model.det_model(image)
            if len(boxes) == 0:
                samples.append({"at_seconds": int(at) / fps, "parts": {}})
                continue
            areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            centers = (boxes[:, 0] + boxes[:, 2]) / (2 * width)
            primary = int(np.argmax(areas * (1 - np.abs(centers - 0.5))))
            points, scores = model.pose_model(image, bboxes=boxes[primary : primary + 1])
            parts = visible_body_parts(points[0].tolist(), scores[0].tolist(), width, height)
            samples.append(
                {
                    "at_seconds": int(at) / fps,
                    "parts": parts,
                    "box": boxes[primary].tolist(),
                    "landmarks": points[0].tolist(),
                    "confidence": scores[0].tolist(),
                }
            )
    finally:
        capture.release()
    motion = background_motion(np.stack(frames))
    return {
        "revision": spec["revision"],
        "device": "cpu",
        "shot_size": framing_from_parts([sample["parts"] for sample in samples]),
        "camera_lateral_direction": camera_lateral_direction(motion),
        "body_samples": samples,
        "background_motion": motion,
    }


def run_geometry(video: Path, root: Path) -> dict[str, Any]:
    spec = specification()
    child = subprocess.run(
        [str(root / spec["python"]), "-B", "-m", "services.local_models.geometry_tool", str(video)],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "CUDA_VISIBLE_DEVICES": "",
            "OMP_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
        },
        capture_output=True,
        text=True,
        timeout=75,
        check=True,
    )
    return dict(json.loads(child.stdout))


if __name__ == "__main__":
    print(json.dumps(measure(Path(sys.argv[1]), Path(os.environ["CAMCAT_STUDIO_DATA"]))))
