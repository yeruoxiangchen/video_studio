from types import SimpleNamespace

import pytest

from services.local_models.asr_contract import transcript_packet


def test_multilingual_transcript_keeps_real_text_times_and_detected_language():
    segments = iter(
        [
            SimpleNamespace(text=" Welcome back. ", start=0.4, end=1.7),
            SimpleNamespace(text=" Let's edit a video. ", start=2.1, end=3.9),
        ]
    )
    info = SimpleNamespace(language="en", language_probability=0.98)
    packet = transcript_packet(segments, info)
    assert packet["text"] == "Welcome back. Let's edit a video."
    assert packet["segments"] == [
        {"text": "Welcome back.", "start": 0.4, "end": 1.7},
        {"text": "Let's edit a video.", "start": 2.1, "end": 3.9},
    ]
    assert packet["language"] == "en"
    assert packet["language_probability"] == 0.98


def test_no_speech_stays_empty_without_a_fabricated_cue():
    packet = transcript_packet(iter([]), SimpleNamespace(language="zh", language_probability=0.6))
    assert packet["text"] == ""
    assert packet["segments"] == []


def test_chinese_output_is_canonical_and_keeps_the_raw_transcription_for_audit():
    raw = "遊客乘船觀賞兩岸風景，2026 年開放。"
    packet = transcript_packet(
        [SimpleNamespace(text=raw, start=0.2, end=3.4)],
        SimpleNamespace(language="zh", language_probability=0.99),
    )
    assert packet["text"] == "游客乘船观赏两岸风景，2026 年开放。"
    assert packet["segments"][0] == {
        "text": packet["text"],
        "start": 0.2,
        "end": 3.4,
    }
    assert packet["raw_text"] == raw
    assert packet["normalization"] == "OpenCC-1.1.9:t2s"


@pytest.mark.parametrize("start,end", [(-0.1, 2), (3, 2), (0, float("inf"))])
def test_invalid_model_timecodes_fail_the_transcript_contract(start, end):
    with pytest.raises(ValueError, match="timestamp"):
        transcript_packet(
            iter([SimpleNamespace(text="speech", start=start, end=end)]),
            SimpleNamespace(language="en", language_probability=0.9),
        )


def test_real_word_confidences_survive_canonicalization_and_keep_native_timestamps():
    segment = SimpleNamespace(
        text=" 遊客到訪 CamCat。",
        start=0.2,
        end=2.5,
        words=[
            SimpleNamespace(word="遊客", start=0.2, end=0.8, probability=0.94),
            SimpleNamespace(word="到訪", start=0.8, end=1.2, probability=0.32),
            SimpleNamespace(word=" CamCat。", start=1.2, end=2.5, probability=0.88),
        ],
    )
    packet = transcript_packet([segment], SimpleNamespace(language="zh", language_probability=0.99))
    assert packet["segments"][0]["words"][0] == {
        "word": "游客",
        "start": 0.2,
        "end": 0.8,
        "probability": 0.94,
    }
    assert packet["segments"][0]["words"][1]["probability"] == 0.32
    assert packet["segments"][0]["words"][2]["word"] == " CamCat。"
    assert packet["quality"]["word_count"] == 3
    assert packet["quality"]["low_confidence_word_count"] == 1
    assert packet["quality"]["mean_word_probability"] == pytest.approx((0.94 + 0.32 + 0.88) / 3)
    assert packet["quality"]["review_recommended"] is True


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_word_confidence_is_rejected(probability):
    segment = SimpleNamespace(
        text="speech",
        start=0,
        end=1,
        words=[SimpleNamespace(word="speech", start=0, end=1, probability=probability)],
    )
    with pytest.raises(ValueError, match="confidence"):
        transcript_packet([segment], SimpleNamespace(language="en", language_probability=0.99))


def test_public_transcript_retains_quality_without_private_worker_fields():
    from services.local_models.asr_contract import public_transcript

    event = {
        "event": "result",
        "request_id": "private",
        "path": "/private/input.wav",
        "text": "Hello",
        "segments": [],
        "language": "en",
        "model": "actual-model",
        "language_probability": 0.99,
        "quality": {"word_count": 1},
        "runtime": {"device": "cuda"},
        "raw_text": "Hello",
        "normalization": None,
    }
    result = public_transcript(event)
    assert result["quality"] == {"word_count": 1}
    assert result["runtime"] == {"device": "cuda"}
    assert result["raw_text"] == "Hello"
    assert not {"event", "request_id", "path"} & result.keys()
