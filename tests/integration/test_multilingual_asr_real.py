"""Real local speech recognition with lexical accuracy and language evidence."""

import json
import os
import re
import wave
from pathlib import Path

import httpx
import pytest
from camcat.config import get_settings
from camcat.localization.service import SpeechClient
from camcat.services.providers import QwenAsrClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("CAMCAT_LOCAL_MODELS_EXTERNAL") != "1",
        reason="requires real local ASR and Piper services",
    ),
]


def test_real_english_recognition_preserves_language_and_spoken_words(tmp_path: Path):
    settings = get_settings()
    source = tmp_path / "english.wav"
    spoken = "The museum opens at nine. Please bring your camera and follow the guide."
    SpeechClient(settings).synthesize(spoken, source, voice=settings.tts_voice)
    result = QwenAsrClient(settings).transcribe(source)
    (tmp_path / "transcript.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    words = set(re.findall(r"[a-z]+", spoken.lower()))
    heard = set(re.findall(r"[a-z]+", result["text"].lower()))
    recall = len(words & heard) / len(words)
    assert recall >= 0.8, {"lexical_recall": recall, "text": result["text"]}
    assert re.search(r"[\u4e00-\u9fff]", result["text"]) is None
    assert result["language"] == "en"
    assert result["language_probability"] > 0.5
    assert result["segments"]
    assert all(0 <= s["start"] < s["end"] for s in result["segments"])
    words = [word for segment in result["segments"] for word in segment["words"]]
    assert result["quality"]["word_count"] == len(words) > 0
    assert all(0 <= word["probability"] <= 1 for word in words)
    assert 0 <= result["quality"]["mean_word_probability"] <= 1


def test_bad_audio_is_rejected_and_the_following_real_transcription_recovers(tmp_path: Path):
    settings = get_settings()
    with httpx.Client(
        base_url=settings.asr_base_url,
        headers={"Authorization": "Bearer " + settings.asr_api_key.get_secret_value()},
        trust_env=False,
        timeout=60,
    ) as client:
        rejected = client.post(
            "/v1/audio/transcriptions",
            data={"model": settings.asr_model},
            files={"file": ("broken.mp3", b"not an audio file", "audio/mpeg")},
        )
        assert rejected.status_code == 422
    source = tmp_path / "after-invalid.wav"
    SpeechClient(settings).synthesize(
        "The guide opens the museum.", source, voice=settings.tts_voice
    )
    result = QwenAsrClient(settings).transcribe(source)
    assert "museum" in result["text"].lower()


def test_real_silence_does_not_create_spoken_text(tmp_path: Path):
    source = tmp_path / "silence.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 32000)
    result = QwenAsrClient(get_settings()).transcribe(source)
    assert result["text"] == ""
    assert result["segments"] == []


def test_unknown_asr_model_reports_the_actual_local_model():
    settings = get_settings()
    with httpx.Client(
        base_url=settings.asr_base_url,
        headers={"Authorization": "Bearer " + settings.asr_api_key.get_secret_value()},
        trust_env=False,
        timeout=30,
    ) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            data={"model": "unconfigured-model"},
            files={"file": ("unused.wav", b"invalid", "audio/wav")},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "asr_model_mismatch",
        "expected_model": settings.asr_model,
        "retryable": False,
    }


@pytest.mark.skipif(
    not os.getenv("CAMCAT_EXTERNAL_CHINESE_AUDIO"),
    reason="requires the recorded Mandarin audio fixture",
)
def test_real_mandarin_language_and_source_timestamps():
    source = Path(os.environ["CAMCAT_EXTERNAL_CHINESE_AUDIO"])
    result = QwenAsrClient(get_settings()).transcribe(source)
    assert result["language"] == "zh"
    assert result["language_probability"] > 0.5
    assert len(re.findall(r"[\u4e00-\u9fff]", result["text"])) >= 8
    assert result["segments"]
    assert all(0 <= s["start"] < s["end"] for s in result["segments"])
