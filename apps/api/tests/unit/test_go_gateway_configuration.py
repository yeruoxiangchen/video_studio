import pytest


def connection():
    return {
        "api_key": "unit-gateway-key",
        "providers": [
            {"role": "LLM", "upstream_model": "planning-model", "alias": "video-planning"},
            {
                "role": "TRANSLATION",
                "upstream_model": "translation-model",
                "alias": "video-translation",
            },
        ],
    }


def test_local_reconfiguration_preserves_connected_gateway_roles():
    from scripts.configure_local_deployment import with_gateway_profile

    original = {
        "CAMCAT_LLM_MODEL": "planning-model",
        "CAMCAT_TRANSLATION_MODEL": "translation-model",
    }
    result = with_gateway_profile(original, connection())
    assert original["CAMCAT_LLM_MODEL"] == "planning-model"
    assert result["CAMCAT_LLM_MODEL"] == "video-planning"
    assert result["CAMCAT_TRANSLATION_MODEL"] == "video-translation"
    assert result["CAMCAT_LLM_BASE_URL"] == "http://10.0.2.2:29480"
    assert result["CAMCAT_LLM_API_KEY"] == "unit-gateway-key"
    assert result["CAMCAT_LLM_HEALTH_PATH"] == "/v1/models"
    assert result["CAMCAT_LLM_MAX_OUTPUT_TOKENS"] == "4096"


def test_gateway_model_change_requires_explicit_channel_update():
    from scripts.configure_local_deployment import with_gateway_profile

    with pytest.raises(ValueError, match="channel"):
        with_gateway_profile({"CAMCAT_LLM_MODEL": "different-model"}, connection())


def test_unconnected_deployment_keeps_direct_configuration():
    from scripts.configure_local_deployment import with_gateway_profile

    original = {"CAMCAT_LLM_BASE_URL": "http://direct-provider"}
    assert with_gateway_profile(original, None) == original
