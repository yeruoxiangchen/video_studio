import pytest


def test_local_reconfiguration_preserves_the_selected_specialist_provider():
    from scripts.configure_local_deployment import translation_configuration

    configured = {
        "CAMCAT_TRANSLATION_BACKEND": "hymt",
        "CAMCAT_TRANSLATION_BASE_URL": "http://10.0.2.2:18976",
        "CAMCAT_TRANSLATION_API_KEY": "private-unit-key",
        "CAMCAT_TRANSLATION_MODEL": "tencent/Hy-MT2-1.8B",
    }
    assert translation_configuration(configured, "core-key") == configured


def test_local_configuration_requires_a_complete_specialist_selection():
    from scripts.configure_local_deployment import translation_configuration

    with pytest.raises(ValueError):
        translation_configuration({"CAMCAT_TRANSLATION_BACKEND": "hymt"}, "core-key")


def test_initial_local_configuration_keeps_the_explicit_chat_backend():
    from scripts.configure_local_deployment import translation_configuration

    result = translation_configuration({}, "core-key")
    assert result["CAMCAT_TRANSLATION_BACKEND"] == "chat"
    assert result["CAMCAT_TRANSLATION_BASE_URL"] == "http://10.0.2.2:18969"


def test_host_integration_routes_translation_to_its_selected_provider():
    from scripts.run_local_integration import provider_urls

    result = provider_urls({"CAMCAT_TRANSLATION_BASE_URL": "http://10.0.2.2:18976"})
    assert result["CAMCAT_TRANSLATION_BASE_URL"] == "http://127.0.0.1:18976"
    assert result["CAMCAT_EMBEDDING_BASE_URL"] == "http://127.0.0.1:18969"


def test_core_deadline_profile_uses_the_actual_core_health_model():
    from scripts.run_full_validation import core_generation_environment

    result = core_generation_environment(
        "http://127.0.0.1:18969",
        {"visual": "actual-core-model"},
        "private-core-key",
    )
    assert result["CAMCAT_LLM_MODEL"] == "actual-core-model"
    assert result["CAMCAT_LLM_BASE_URL"] == "http://127.0.0.1:18969"
