"""Stable transcript contract for the official multilingual Whisper implementation."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, NotRequired, TypedDict

ASR_MODEL = "dropbox-dash/faster-whisper-large-v3-turbo"


class TranscriptWord(TypedDict):
    word: str
    start: float
    end: float
    probability: float


class TranscriptCue(TypedDict):
    text: str
    start: float
    end: float
    words: NotRequired[list[TranscriptWord]]


def public_transcript(event: dict[str, Any]) -> dict[str, Any]:
    """Expose transcript evidence while keeping private transport fields internal."""
    fields = (
        "text",
        "segments",
        "model",
        "language",
        "language_probability",
        "raw_text",
        "normalization",
        "quality",
        "runtime",
    )
    return {name: event[name] for name in fields if name in event}


def transcript_packet(segments: Iterable[Any], info: Any) -> dict[str, Any]:
    cues: list[TranscriptCue] = []
    probabilities: list[float] = []
    for segment in segments:
        text = str(segment.text).strip()
        if not text:
            continue
        start, end = float(segment.start), float(segment.end)
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
            raise ValueError("ASR returned an invalid timestamp")
        cue: TranscriptCue = {"text": text, "start": start, "end": end}
        if getattr(segment, "words", None):
            words: list[TranscriptWord] = []
            for word in segment.words:
                probability = float(word.probability)
                if not math.isfinite(probability) or not 0 <= probability <= 1:
                    raise ValueError("ASR returned an invalid word confidence")
                word_start, word_end = float(word.start), float(word.end)
                if not (
                    math.isfinite(word_start)
                    and math.isfinite(word_end)
                    and 0 <= word_start <= word_end
                ):
                    raise ValueError("ASR returned an invalid word timestamp")
                words.append(
                    {
                        "word": str(word.word),
                        "start": word_start,
                        "end": word_end,
                        "probability": probability,
                    }
                )
                probabilities.append(probability)
            cue["words"] = words
        cues.append(cue)
    raw_text = " ".join(cue["text"] for cue in cues)
    normalization = None
    if info.language == "zh":
        from opencc import OpenCC

        converter = OpenCC("t2s")
        for cue in cues:
            cue["text"] = converter.convert(cue["text"])
            for word in cue.get("words", []):
                word["word"] = converter.convert(word["word"])
        normalization = "OpenCC-1.1.9:t2s"
    return {
        "text": " ".join(cue["text"] for cue in cues),
        "segments": cues,
        "language": str(info.language),
        "language_probability": float(info.language_probability),
        "model": ASR_MODEL,
        **({"raw_text": raw_text, "normalization": normalization} if normalization else {}),
        **(
            {
                "quality": {
                    "word_count": len(probabilities),
                    "mean_word_probability": sum(probabilities) / len(probabilities),
                    "low_confidence_word_count": sum(p < 0.6 for p in probabilities),
                    "review_recommended": any(p < 0.6 for p in probabilities),
                    "review_threshold": 0.6,
                }
            }
            if probabilities
            else {}
        ),
    }
