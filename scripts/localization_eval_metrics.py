"""Reference-only scoring and safe input extraction for the public localization pilot."""

from __future__ import annotations

import os
import tarfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any


def evaluation_protocol(source: Path) -> Path:
    import hashlib
    import json

    explicit = os.environ.get("CAMCAT_VALIDATION_CONFIG")
    if explicit:
        return Path(explicit)
    root = os.environ.get("CAMCAT_STUDIO_DATA")
    marker = Path(root) / "config/active-validation.json" if root else None
    if marker and marker.is_file():
        selected = json.loads(marker.read_text())
        path = Path(selected["protocol"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != selected["sha256"]:
            raise ValueError("Accepted validation protocol digest changed")
        return path
    return source / "eval/localization_public_pilot_v1.json"


def normalize_text(text: str, *, characters: bool) -> str:
    value = unicodedata.normalize("NFKC", text).lower()
    value = "".join(" " if unicodedata.category(c).startswith(("P", "S")) else c for c in value)
    return "".join(value.split()) if characters else " ".join(value.split())


def aligned_hypotheses(references: dict[str, str], hypotheses: dict[str, str]) -> list[str]:
    if not references or set(hypotheses) - set(references):
        raise ValueError("References must be nonempty; hypotheses cannot have unknown sample IDs")
    return [hypotheses.get(key, "") for key in references]


def score_translations(references: dict[str, str], hypotheses: dict[str, str]) -> dict[str, Any]:
    from sacrebleu.metrics import BLEU, CHRF

    predicted = aligned_hypotheses(references, hypotheses)
    refs = [list(references.values())]
    bleu, chrf = BLEU(tokenize="13a"), CHRF(word_order=2)
    b, c = bleu.corpus_score(predicted, refs), chrf.corpus_score(predicted, refs)
    nonempty = sum(bool(t.strip()) for t in predicted)
    return {
        "total": len(references),
        "nonempty": nonempty,
        "missing": len(references) - nonempty,
        "bleu": b.score,
        "chrf_pp": c.score,
        "bleu_signature": str(bleu.get_signature()),
        "chrf_signature": str(chrf.get_signature()),
    }


def score_speech(
    references: dict[str, str], hypotheses: dict[str, str], *, characters: bool
) -> dict[str, Any]:
    import jiwer

    predicted = aligned_hypotheses(references, hypotheses)
    refs = [normalize_text(t, characters=characters) for t in references.values()]
    predicted = [normalize_text(t, characters=characters) for t in predicted]
    result = (jiwer.process_characters if characters else jiwer.process_words)(refs, predicted)
    return {
        "total": len(refs),
        "missing": sum(not t for t in predicted),
        "error_rate": result.cer if characters else result.wer,
        "substitutions": result.substitutions,
        "deletions": result.deletions,
        "insertions": result.insertions,
        "hits": result.hits,
        "normalization": (
            "NFKC, lowercase, Unicode punctuation/symbols to spaces; CER removes spaces"
        ),
    }


def extract_selected_audio(archive: Path, wanted: set[str], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    found: set[str] = set()
    with tarfile.open(archive, "r|gz") as package:
        for member in package:
            path = PurePosixPath(member.name)
            if path.name not in wanted:
                continue
            if path.is_absolute() or ".." in path.parts or not member.isfile():
                raise ValueError("unsafe selected audio archive member")
            if path.name in found or member.size > 25 * 1024 * 1024:
                raise ValueError("duplicate or oversized audio archive member")
            stream = package.extractfile(member)
            if stream is None:
                raise ValueError("selected audio member has no data")
            with stream, (output / path.name).open("wb") as target:
                while block := stream.read(1024 * 1024):
                    target.write(block)
            found.add(path.name)
    if found != wanted:
        raise ValueError("missing selected public audio recordings")


def suite_summary(stages: list[dict[str, Any]], public: dict[str, Any]) -> dict[str, Any]:
    return {
        "automated_functional_pass": bool(stages) and all(s.get("passed") is True for s in stages),
        "failed_stages": [s["name"] for s in stages if s.get("passed") is not True],
        "public_editing_quality_pass": (
            public.get("tasks", 0) > 0
            and public.get("full_reward") == public.get("tasks")
            and public.get("execution_errors") == 0
        ),
        "manual_review_pending": True,
    }


def validate_srt(text: str, cues: list[dict[str, Any]]) -> None:
    import pysubs2

    subtitles = pysubs2.SSAFile.from_string(text, format_="srt")
    if len(subtitles) != len(cues):
        raise ValueError("SRT content has a different cue count")
    for item, cue in zip(subtitles, cues, strict=True):
        if (
            abs(item.start * 1000 - cue["start_us"]) > 1000
            or abs(item.end * 1000 - cue["start_us"] - cue["duration_us"]) > 1000
        ):
            raise ValueError("SRT timing differs from the saved localization cues")
        if " ".join(item.plaintext.split()) != " ".join(cue["text"].split()):
            raise ValueError("SRT content differs from the actual translation")
