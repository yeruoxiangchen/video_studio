import pytest
from camcat.domain.localization import LocalizationCue


def cue(identifier="a", source="请在 9 点到 CamCat。"):
    return LocalizationCue(
        cue_id=identifier,
        start_us=0,
        duration_us=2_000_000,
        source_text=source,
        text=source,
    )


def test_specialist_prompt_uses_full_language_name_and_keeps_source_whole():
    from camcat.localization.hymt import translation_prompt

    source = cue()
    prompt = translation_prompt([source], 0, language="en", glossary={})
    assert prompt.endswith(source.source_text)
    assert "英语" in prompt
    assert "2.0" not in prompt
    assert "shorten" not in prompt


def test_specialist_prompt_uses_only_supplied_neighbor_context_and_terminology():
    from camcat.localization.hymt import translation_prompt

    prompt = translation_prompt(
        [cue("a", "今天介绍 CamCat。"), cue("b", "它有三个步骤。")],
        1,
        language="en",
        glossary={"CamCat": "CamCat Studio"},
    )
    assert "今天介绍 CamCat。" in prompt
    assert "CamCat Studio" in prompt
    assert prompt.endswith("它有三个步骤。")


def test_shortening_requires_explicit_timing_feedback_and_keeps_current_translation():
    from camcat.localization.hymt import translation_prompt

    source = cue().model_copy(update={"text": "Please arrive at CamCat at nine o'clock."})
    prompt = translation_prompt(
        [source],
        0,
        language="en",
        glossary={},
        timing_feedback={"window_seconds": 2, "natural_seconds": 4, "maximum_speed": 1.2},
    )
    assert source.text in prompt and "2" in prompt
    assert "shorten" in prompt.lower()


@pytest.mark.parametrize(
    "message,finish",
    [
        ({"content": ""}, "stop"),
        ({"content": "incomplete"}, "length"),
        ({"content": "<think>unfinished"}, "stop"),
    ],
)
def test_incomplete_specialist_output_is_rejected(message, finish):
    from camcat.localization.hymt import translation_text

    with pytest.raises(ValueError):
        translation_text({"choices": [{"message": message, "finish_reason": finish}]})


def test_specialist_text_keeps_punctuation_and_validated_plain_content():
    from camcat.localization.hymt import translation_text

    assert (
        translation_text(
            {
                "choices": [
                    {
                        "message": {"content": "  CamCat opens at 9.  "},
                        "finish_reason": "stop",
                    }
                ]
            }
        )
        == "CamCat opens at 9."
    )
