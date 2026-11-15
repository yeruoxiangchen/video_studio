"""Check the versions and optimized kernels actually loaded by the visual service."""

import json
import os
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.external]


def test_live_quality_runtime_matches_its_pinned_model_and_compiler():
    endpoint = os.environ.get("CAMCAT_QUALITY_VISUAL_BASE_URL")
    if not endpoint:
        pytest.skip("Set CAMCAT_QUALITY_VISUAL_BASE_URL to the real quality runtime")
    with httpx.Client(trust_env=False, timeout=10) as client:
        response = client.get(endpoint.rstrip("/") + "/health")
        response.raise_for_status()
    actual = response.json()
    expected = json.loads(Path("infra/visual-model.lock.json").read_text())
    assert actual["visual"] == expected["repository"]
    assert actual["revision"] == expected["revision"]
    assert actual["precision"] == expected["precision"]
    assert actual["libraries"]["xgrammar"] == "0.2.9"
    assert actual["optimized_linear_attention"] is True
