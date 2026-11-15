import pytest

from services.local_models.source_geometry import (
    camera_lateral_direction,
    framing_from_parts,
    refine_source_semantics,
    visible_body_parts,
)


def test_offscreen_high_confidence_joints_do_not_establish_visible_knees() -> None:
    points = [[100.0, 80.0] for _ in range(17)]
    scores = [0.9] * 17
    for index in [13, 14, 15, 16]:
        points[index] = [100.0, 400.0]
    parts = visible_body_parts(points, scores, 320, 240)
    assert parts["hips"] and not parts["knees"] and not parts["ankles"]
    assert framing_from_parts([parts] * 5) == "medium"


def test_full_body_measurements_and_knee_up_measurements_are_distinct() -> None:
    assert (
        framing_from_parts([{"ankles": True, "knees": True, "hips": True, "shoulders": True}] * 5)
        == "wide"
    )
    assert (
        framing_from_parts([{"ankles": False, "knees": True, "hips": True, "shoulders": True}] * 5)
        == "medium wide"
    )
    assert framing_from_parts([]) is None


@pytest.mark.parametrize("dx,expected", [(0.01, "left"), (-0.01, "right")])
def test_consistent_background_translation_has_the_opposite_apparent_camera_direction(
    dx: float, expected: str
) -> None:
    data = {
        "working_width": 384,
        "working_height": 216,
        "total_pairs": 15,
        "steps": [{"center_dx": dx, "center_dy": 0, "scale": 1, "inlier_fraction": 0.9}] * 15,
    }
    assert camera_lateral_direction(data) == expected


def test_zoom_and_inconsistent_drift_cannot_be_promoted_to_lateral_motion() -> None:
    steps = [{"center_dx": 0.002, "center_dy": 0, "scale": 1.04, "inlier_fraction": 0.95}] * 15
    assert camera_lateral_direction({"steps": steps, "total_pairs": 15}) == "uncertain"
    for index, item in enumerate(steps):
        steps[index] = {**item, "center_dx": 0.01 if index % 2 else -0.01, "scale": 1}
    assert camera_lateral_direction({"steps": steps, "total_pairs": 15}) == "uncertain"


def test_real_geometry_can_refine_fields_without_mutating_the_model_observations() -> None:
    model = {"shot_size": "medium wide", "camera_motion": "track right", "description": "A person"}
    corrected = refine_source_semantics(
        model, {"shot_size": "medium close-up", "camera_lateral_direction": "left"}
    )
    assert (
        corrected["shot_size"] == "medium close-up" and corrected["camera_motion"] == "track left"
    )
    assert model["camera_motion"] == "track right"
