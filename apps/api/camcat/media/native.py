"""Typed adapter for the separately built, real FFmpeg/pybind11 extension."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class SampledFrame:
    pixels: NDArray[np.uint8]
    seconds: float


def sample_frames(
    source: Path,
    times: Sequence[float],
    *,
    width: int = 512,
    threads: int = 2,
    timeout: float = 60.0,
) -> list[SampledFrame]:
    try:
        import _camcat_media_native as implementation
    except ImportError as error:
        raise RuntimeError(
            "Native media extension is unavailable; install native/media "
            "with FFmpeg development libraries"
        ) from error
    rows: list[dict[str, Any]] = implementation.sample_frames(
        str(source), list(times), width, threads, timeout
    )
    return [SampledFrame(pixels=row["pixels"], seconds=float(row["seconds"])) for row in rows]
