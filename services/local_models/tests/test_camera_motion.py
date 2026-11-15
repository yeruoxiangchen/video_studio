"""Pure image-geometry unit tests, not a model or public benchmark result."""

import cv2
import numpy as np
from numpy.typing import NDArray

from services.local_models.camera_motion import background_motion


def frames(scale_per_step: float) -> NDArray[np.uint8]:
    rng = np.random.default_rng(17)
    image = rng.integers(0, 256, (240, 384, 3), dtype=np.uint8)
    image = cv2.GaussianBlur(image, (5, 5), 0)
    sequence = []
    for i in range(8):
        matrix = cv2.getRotationMatrix2D((192, 120), 0, scale_per_step**i)
        sequence.append(cv2.warpAffine(image, matrix, (384, 240)))
    return np.stack(sequence)


def test_background_contraction_has_scale_below_one() -> None:
    result = background_motion(frames(0.98))
    assert result["valid_pairs"] >= 5
    assert result["scale_change"] < 0.95


def test_background_expansion_has_scale_above_one() -> None:
    result = background_motion(frames(1.02))
    assert result["valid_pairs"] >= 5
    assert result["scale_change"] > 1.05


def test_unchanged_background_stays_near_unit_scale() -> None:
    result = background_motion(frames(1))
    assert abs(result["scale_change"] - 1) < 0.001


def test_blank_frames_return_unknown_instead_of_inventing_camera_motion() -> None:
    result = background_motion(np.zeros((4, 128, 192, 3), dtype=np.uint8))
    assert result["valid_pairs"] == 0
    assert result["scale_change"] is None
