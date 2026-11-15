"""Real automatic observations of the existing task's visually reviewed failures.

The model sees the original video and the generic analysis prompt only. Expected
properties below are assertions, never supplied to its prompt or editing planner.
"""

import json
import os
import re
from pathlib import Path

import pytest
from camcat.config import get_settings
from camcat.services.providers import (
    SOURCE_VISUAL_PROMPT,
    QwenVisualAnalysisClient,
    source_visual_schema,
)

pytestmark = [pytest.mark.integration, pytest.mark.external]


@pytest.fixture
def perceive():
    folder = os.environ.get("CAMCAT_PERCEPTION_FIXTURE_DIR")
    if not folder:
        pytest.skip("Set CAMCAT_PERCEPTION_FIXTURE_DIR to the existing assembly originals")
    client = QwenVisualAnalysisClient(get_settings())

    def analyze(filename):
        path = Path(folder) / filename
        assert path.is_file(), "Expected existing public development fixture is missing"
        return client.analyze_video(path, compact=True)

    return analyze


def test_outward_background_change_is_not_reported_as_static(perceive):
    observed = perceive("5.mp4")
    assert observed.camera_motion in {"pull out", "dolly backward"}


def test_running_subject_is_not_reduced_to_a_walking_then_stopping_template(perceive):
    observed = perceive("11.mp4")
    facts = " ".join([observed.description, *observed.actions, *observed.temporal_evidence])
    assert re.search(r"\b(run|runs|running|sprint|sprints|sprinting)\b|跑", facts, re.I)


def test_a_rear_upper_body_view_is_not_labelled_a_full_body_wide_shot(perceive):
    observed = perceive("12.mp4")
    assert observed.shot_size in {"medium close-up", "medium", "medium wide"}


def test_compact_analysis_includes_actual_cpu_geometry_provenance():
    folder = os.environ.get("CAMCAT_PERCEPTION_FIXTURE_DIR")
    if not folder:
        pytest.skip("Set CAMCAT_PERCEPTION_FIXTURE_DIR to the existing assembly originals")
    client = QwenVisualAnalysisClient(get_settings())
    path = Path(folder) / "8.mp4"
    with path.open("rb") as stream:
        response = client.request(
            "POST",
            "/v1/analyze",
            data={
                "model": client.model,
                "prompt": SOURCE_VISUAL_PROMPT,
                "response_schema": json.dumps(source_visual_schema()),
                "max_tokens": "1536",
            },
            files={"video": (path.name, stream, "video/mp4")},
        )
    geometry = response["camcat_geometry"]
    assert geometry["device"] == "cpu"
    assert len(geometry["body_samples"]) >= 3
    assert geometry["background_motion"]["valid_pairs"] >= 8
    assert geometry["shot_size"] == response["analysis"]["shot_size"]
    assert geometry["camera_lateral_direction"] in {"left", "right", "uncertain"}


def test_a_continuous_motion_clip_retains_complete_opening_and_closing_observations():
    folder = os.environ.get("CAMCAT_NARRATIVE_FIXTURE_DIR")
    if not folder:
        pytest.skip("Set CAMCAT_NARRATIVE_FIXTURE_DIR to the existing narrative originals")
    client = QwenVisualAnalysisClient(get_settings())
    observed = client.analyze_video(Path(folder) / "11.mp4", compact=True)
    assert len(observed.temporal_evidence) == 2
    assert all(len(clause.split()) >= 2 for clause in observed.temporal_evidence)
    assert re.search(r"motorcycle|wheel|motorbike", " ".join(observed.temporal_evidence), re.I)
