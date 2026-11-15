from __future__ import annotations

import pytest
from camcat.local_providers.speech import SpeechRequest
from pydantic import ValidationError


def test_speech_request_rejects_empty_or_oversized_text() -> None:
    for text in ("", " ", "a" * 2001):
        with pytest.raises(ValidationError):
            SpeechRequest(model="piper", voice="en_US-lessac-medium", input=text)


def test_speech_request_accepts_only_the_wav_contract() -> None:
    request = SpeechRequest(model="piper", voice="en_US-lessac-medium", input="Hello.")
    assert request.response_format == "wav"
    with pytest.raises(ValidationError):
        SpeechRequest(
            model="piper", voice="en_US-lessac-medium", input="Hello.", response_format="mp3"
        )
