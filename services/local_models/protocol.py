from __future__ import annotations

import base64
import logging
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


def local_gpu_plan(
    devices: list[dict[str, Any]],
    *,
    preferred_indices: list[int] | None = None,
    visual_precision: Literal["nf4", "bfloat16"] = "nf4",
) -> list[dict[str, Any]]:
    if visual_precision not in {"nf4", "bfloat16"}:
        raise ValueError("Unsupported visual model precision")
    required = 3 if visual_precision == "bfloat16" else 2
    if preferred_indices is not None and (
        len(preferred_indices) != required or len(set(preferred_indices)) != required
    ):
        raise ValueError(f"The {visual_precision} profile requires {required} distinct GPU indices")
    idle = sorted(
        (device for device in devices if device["free_mib"] >= 22528),
        key=lambda device: (-device["free_mib"], device["index"]),
    )
    if preferred_indices is not None:
        available = {device["index"]: device for device in idle}
        if any(index not in available for index in preferred_indices):
            raise RuntimeError(
                f"The requested {required} GPUs must each have at least 22528 MiB free"
            )
        idle = [available[index] for index in preferred_indices]
    if len(idle) < required:
        label = "Two" if required == 2 else "3"
        raise RuntimeError(f"{label} idle GPUs with at least 22528 MiB free are required")
    plan = []
    for role, logical, precision in (
        ("embedding", 0, "bfloat16"),
        ("reranker", 1, "nf4"),
        ("visual", 2 if visual_precision == "bfloat16" else 1, visual_precision),
    ):
        device = idle[logical]
        plan.append(
            {
                **device,
                "role": role,
                "cuda_device": logical,
                "precision": precision,
                "memory_fraction": 0.86,
                "budget_mib": round(device["total_mib"] * 0.86),
                "budget_shared": visual_precision == "nf4" and logical == 1,
            }
        )
    return plan


def normalize_mrl(values: list[float]) -> list[float]:
    if len(values) != 4096 or not all(math.isfinite(value) for value in values):
        raise ValueError("Expected the finite 4096-dimensional canonical 8B output")
    selected = values[:2048]
    norm = math.hypot(*selected)
    if norm <= 0:
        raise ValueError("Canonical model returned a zero MRL vector")
    return [value / norm for value in selected]


class MediaObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, max_length=8000)
    image_base64: str | None = Field(default=None, max_length=12 * 1024 * 1024)
    video_url: HttpUrl | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_media(self) -> MediaObject:
        if not ((self.text and self.text.strip()) or self.image_base64 or self.video_url):
            raise ValueError("A real text, image or video input is required")
        if self.image_base64 and not self.image_base64.startswith("data:image/"):
            raise ValueError("image_base64 must contain inline image data")
        return self

    def official(self) -> dict[str, Any]:
        output: dict[str, Any] = {}
        if self.text and self.text.strip():
            output["text"] = self.text
        if self.image_base64:
            output["image"] = self.image_base64
        if self.video_url:
            output["video"] = str(self.video_url)
        return output

    def materialize(self, folder: Path, stem: str) -> dict[str, Any]:
        output = self.official()
        if self.image_base64:
            header, encoded = self.image_base64.split(";base64,", 1)
            extensions = {
                "data:image/jpeg": ".jpg",
                "data:image/png": ".png",
                "data:image/webp": ".webp",
                "data:image/bmp": ".bmp",
            }
            if header not in extensions:
                raise ValueError("Unsupported inline image format")
            decoded = base64.b64decode(encoded, validate=True)
            if not decoded:
                raise ValueError("Empty inline image")
            path = folder / (stem + extensions[header])
            path.write_bytes(decoded)
            output["image"] = str(path)
        return output


class RerankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Literal["Qwen/Qwen3-VL-Reranker-8B"] = "Qwen/Qwen3-VL-Reranker-8B"
    query: MediaObject
    documents: list[MediaObject] = Field(min_length=1, max_length=64)
    top_n: int | None = Field(default=None, ge=1, le=64)
    instruction: str = Field(
        default="Rank source clips for the requested video edit.", max_length=2000
    )


class StrictVisionErrors(logging.Handler):
    """Abort the official wrapper's NULL-input fallback without changing its model math."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            raise ValueError("Official image/video preprocessing failed; result was rejected")
