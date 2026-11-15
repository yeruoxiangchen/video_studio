import pytest

from scripts.configure_local_deployment import local_editing_profile, local_visual_profile
from scripts.studio_status import CORE_SERVICES, render_status, required_generation_roles


def test_reconfiguration_preserves_the_explicitly_selected_local_model():
    profile = local_visual_profile("Qwen/Qwen3.5-9B")
    assert profile["CAMCAT_LLM_MODEL"] == "Qwen/Qwen3.5-9B"
    assert profile["CAMCAT_LLM_BASE_URL"] == "http://10.0.2.2:18972"


def test_explicit_legacy_selection_keeps_the_canonical_gateway():
    profile = local_visual_profile("Qwen/Qwen3.5-9B", "Qwen/Qwen3-VL-8B-Instruct")
    assert profile["CAMCAT_LLM_BASE_URL"] == "http://10.0.2.2:18969"


def test_local_editor_selection_preserves_the_native_route_and_can_inherit_vision():
    assert local_editing_profile(None) == {}
    native = local_editing_profile(None, "Qwen/Qwen3.6-27B")
    assert native == {
        "CAMCAT_LLM_MODEL": "Qwen/Qwen3.6-27B",
        "CAMCAT_LLM_BASE_URL": "http://10.0.2.2:18974",
    }
    assert local_editing_profile("Qwen/Qwen3.6-27B") == native
    assert local_editing_profile("Qwen/Qwen3.6-27B", "inherit") == {}


def test_split_editor_and_vision_both_gate_readiness():
    roles = required_generation_roles(
        {"CAMCAT_LLM_MODEL": "Qwen/Qwen3.6-27B", "CAMCAT_VISUAL_ANALYSIS_MODEL": "Qwen/Qwen3.5-9B"}
    )
    assert roles == {"visual_required": True, "narrative_required": True}


def test_unknown_explicit_models_are_rejected_instead_of_mislabeled():
    with pytest.raises(ValueError):
        local_visual_profile(None, "unknown-model")


def test_a_down_selected_visual_provider_prevents_an_overall_ready_claim():
    snapshot = {
        "api_ready": True,
        "docker_ready": True,
        "containers": [
            {"Service": name, "State": "running", "Health": "healthy"} for name in CORE_SERVICES
        ],
        "visual_required": True,
        "visual_api_ready": False,
        "visual_model": {"state": "failed", "visual": "Qwen/Qwen3.5-9B", "gpu_index": 7},
    }
    assert "整站：准备中" in render_status(snapshot)
    snapshot["visual_api_ready"] = True
    snapshot["visual_model"]["state"] = "ready"
    assert "整站：就绪" in render_status(snapshot)


def test_loading_canonical_models_prevent_an_overall_ready_claim():
    snapshot = {
        "api_ready": True,
        "docker_ready": True,
        "containers": [
            {"Service": name, "State": "running", "Health": "healthy"} for name in CORE_SERVICES
        ],
        "core_required": True,
        "core_api_ready": False,
        "local_models": {"state": "loading", "phase": "load_embedding"},
    }
    assert "整站：准备中" in render_status(snapshot)


def test_an_explicit_narrative_provider_has_its_own_local_route():
    from scripts.configure_local_deployment import local_narrative_profile

    assert local_narrative_profile(None) == {
        "CAMCAT_NARRATIVE_BASE_URL": "",
        "CAMCAT_NARRATIVE_MODEL": "",
    }
    active = local_narrative_profile(None, "Qwen/Qwen3.6-27B")
    assert active["CAMCAT_NARRATIVE_BASE_URL"] == "http://10.0.2.2:18974"
    assert local_narrative_profile("Qwen/Qwen3.6-27B") == active


def test_a_down_selected_narrative_provider_prevents_an_overall_ready_claim():
    snapshot = {
        "api_ready": True,
        "docker_ready": True,
        "containers": [
            {"Service": name, "State": "running", "Health": "healthy"} for name in CORE_SERVICES
        ],
        "narrative_required": True,
        "narrative_api_ready": False,
        "narrative_model": {"state": "failed", "model": "Qwen/Qwen3.6-27B", "gpu_index": 0},
    }
    assert "整站：准备中" in render_status(snapshot)
