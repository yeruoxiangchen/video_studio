"""Unit checks for translation request contracts; no external-service acceptance."""

import pytest
from camcat.domain.localization import LocalizationCue


def cues():
    return [
        LocalizationCue(
            cue_id="a", start_us=0, duration_us=2_000_000, source_text="你好", text="你好"
        ),
        LocalizationCue(
            cue_id="b", start_us=2_000_000, duration_us=2_000_000, source_text="欢迎", text="欢迎"
        ),
    ]


def test_translation_schema_binds_the_count_and_ids_without_timecode_fields():
    import jsonschema
    from camcat.localization.contract import translation_response_schema

    schema = translation_response_schema(cues())
    valid = {"translations": [{"id": "a", "text": "Hello"}, {"id": "b", "text": "Welcome"}]}
    jsonschema.validate(valid, schema)
    for invalid in (
        {"description": "Hello"},
        {"translations": [{"id": "unknown", "text": "Hello"}, {"id": "b", "text": "Welcome"}]},
        {"translations": [{"id": "a", "text": "Hello"}]},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)


def test_translation_client_supplies_the_contract_and_retains_measured_windows():
    from camcat.localization.service import TranslationService

    class ContractClient:
        def json_completion(self, *, system, user, response_schema=None, temperature=0.1):
            assert response_schema is not None
            assert response_schema["additionalProperties"] is False
            return {"translations": [{"id": "a", "text": "Hello"}, {"id": "b", "text": "Welcome"}]}

    service = TranslationService.__new__(TranslationService)
    service.backend = "chat"
    service.client = ContractClient()
    before = cues()
    after = service.translate(before, language="en", glossary={})
    assert [(c.cue_id, c.start_us, c.duration_us) for c in after] == [
        (c.cue_id, c.start_us, c.duration_us) for c in before
    ]
    assert [c.text for c in after] == ["Hello", "Welcome"]


def test_chat_translation_preserves_full_meaning_before_explicit_duration_feedback():
    import json

    from camcat.localization.service import TranslationService

    calls = []

    class ContractClient:
        def json_completion(self, **kwargs):
            calls.append(kwargs)
            return {"translations": [{"id": "a", "text": "Hello"}, {"id": "b", "text": "Welcome"}]}

    service = TranslationService.__new__(TranslationService)
    service.backend = "chat"
    service.client = ContractClient()
    service.translate(cues(), language="en", glossary={})
    assert "complete meaning" in calls[0]["system"]
    assert "window" not in calls[0]["system"].lower()
    assert calls[0]["temperature"] == 0
    service.translate(
        cues(),
        language="en",
        glossary={},
        timing_feedback={"window_seconds": 2, "natural_seconds": 4, "maximum_speed": 1.2},
    )
    assert "shorten" in calls[1]["system"].lower()
    assert json.loads(calls[1]["user"])["timing_feedback"]["natural_seconds"] == 4
