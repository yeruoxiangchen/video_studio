import pytest
from camcat.config import Settings


@pytest.mark.parametrize(
    "partial",
    [
        {"narrative_base_url": "http://native.local:18974"},
        {"narrative_model": "Qwen/Qwen3.6-27B"},
        {"narrative_api_key": "unit-private-key"},
    ],
)
def test_a_partially_configured_narrative_provider_fails_before_any_upload(partial):
    with pytest.raises(ValueError, match="narrative"):
        Settings(**partial)


def test_the_optional_narrative_provider_retains_the_general_editing_model():
    settings = Settings(
        llm_model="Qwen/Qwen3.5-9B",
        narrative_base_url="http://native.local:18974",
        narrative_model="Qwen/Qwen3.6-27B",
        narrative_api_key="unit-private-key",
    )
    assert settings.llm_model == "Qwen/Qwen3.5-9B"
    assert settings.narrative_model == "Qwen/Qwen3.6-27B"
