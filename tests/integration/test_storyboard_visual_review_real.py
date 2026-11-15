"""Actual source pixels and the configured native multimodal planning service."""

import json
import os
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.external]


@pytest.mark.skipif(
    not os.getenv("CAMCAT_STORYBOARD_REVIEW_SESSION"),
    reason="requires a real uploaded source session and its user-supplied storyboard",
)
def test_native_storyboard_review_uses_supplied_source_frames(tmp_path):
    from camcat.agent.context import compact_materials
    from camcat.agent.storyboard import storyboard_slots
    from camcat.config import get_settings
    from camcat.domain.project import EditingProjectV2
    from camcat.services.narrative import NativeNarrativePlanner
    from camcat.services.object_store import ObjectStore

    assert callable(NativeNarrativePlanner.plan_storyboard)
    settings = get_settings()
    session = os.environ["CAMCAT_STORYBOARD_REVIEW_SESSION"]
    response = httpx.get(
        f"http://127.0.0.1:18960/api/v1/editing/sessions/{session}", trust_env=False, timeout=30
    )
    response.raise_for_status()
    project = EditingProjectV2.model_validate(response.json()["state"])
    originals = compact_materials([], project)
    originals = [m for m in originals if m["origin"] == "user_upload"]
    instruction = Path(os.environ["CAMCAT_STORYBOARD_INSTRUCTION_PATH"]).read_text()
    planner = NativeNarrativePlanner(settings, object_store=ObjectStore(settings))
    try:
        proposal = planner.plan_storyboard(
            project=project, slots=storyboard_slots(instruction), originals=originals
        )
        assert len(proposal.clips) == len(storyboard_slots(instruction))
        assert len({c.material_id for c in proposal.clips}) == len(proposal.clips)
        (tmp_path / "proposal.json").write_text(
            json.dumps(proposal.model_dump(mode="json"), ensure_ascii=False, indent=2)
        )
    finally:
        planner.client.client.close()
