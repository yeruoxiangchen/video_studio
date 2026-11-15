"""Request validation is independent of external model/service verification."""

import pytest
from camcat.localization.schemas import ApplyLocalizationRequest, DubRequest, TranslateRequest
from pydantic import ValidationError


def test_translation_requires_a_saved_state_version():
    with pytest.raises(ValidationError):
        TranslateRequest(target_language="en")
    with pytest.raises(ValidationError):
        TranslateRequest(base_version=0, target_language="en")


def test_translation_accepts_explicit_terminology():
    request = TranslateRequest(
        base_version=2,
        target_language="en",
        source_track_id="captions",
        glossary={"云栖大会": "Yunqi Conference"},
    )
    assert request.glossary["云栖大会"] == "Yunqi Conference"


def test_dubbing_cannot_exceed_the_original_audio_gain_limit():
    with pytest.raises(ValidationError):
        DubRequest(base_version=2, original_audio_volume=5)
    assert DubRequest(base_version=2).original_audio_volume == 0
    assert DubRequest(base_version=2).allow_duration_rewrite is False


def test_apply_rejects_arbitrary_command_injection():
    with pytest.raises(ValidationError):
        ApplyLocalizationRequest(base_version=2, commands=[{"type": "remove_track"}])


@pytest.mark.parametrize("language", ["", "../secret", "not a language"])
def test_invalid_language_codes_are_rejected(language):
    with pytest.raises(ValidationError):
        TranslateRequest(base_version=1, target_language=language)
