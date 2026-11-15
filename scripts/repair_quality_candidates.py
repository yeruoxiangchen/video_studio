"""Compare real local candidates on the existing fixed inputs, retaining every output."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from scripts.public_benchmark_protocol import write_json


def recognize(root: Path, output: Path, samples: list[dict]) -> None:
    from faster_whisper import WhisperModel
    from huggingface_hub import HfApi, snapshot_download

    repository = "dropbox-dash/faster-whisper-large-v3-turbo"
    info = HfApi().model_info(repository)
    record = {
        "repository": repository,
        "revision": info.sha,
        "device": "cpu",
        "precision": "int8",
        "runtime": "faster-whisper==1.2.1",
        "physical_path": str(root / "models/faster-whisper-large-v3-turbo"),
        "languages": "automatic multilingual detection",
    }
    write_json(output / "model-source.json", record)
    snapshot_download(
        repository,
        revision=info.sha,
        local_dir=record["physical_path"],
        allow_patterns=["*.json", "*.bin", "vocabulary.*", "README.md"],
        max_workers=3,
    )
    write_json(Path(record["physical_path"]) / "camcat-source.json", record)
    model = WhisperModel(
        record["physical_path"],
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
        num_workers=1,
        local_files_only=True,
    )
    for index, sample in enumerate(samples):
        started = time.monotonic()
        segments, info = model.transcribe(
            sample["audio_path"],
            language=None,
            beam_size=5,
            vad_filter=True,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        rows = [
            {
                "text": s.text.strip(),
                "start": s.start,
                "end": s.end,
                "words": [
                    {"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                    for w in s.words or []
                ],
            }
            for s in segments
            if s.text.strip()
        ]
        result = {
            "sentence_id": sample["sentence_id"],
            "text": " ".join(s["text"] for s in rows),
            "segments": rows,
            "language": info.language,
            "language_probability": info.language_probability,
            "model": repository,
            "revision": record["revision"],
            "seconds": time.monotonic() - started,
        }
        write_json(output / (sample["sentence_id"] + ".json"), result)
        print(
            json.dumps(
                {
                    "phase": "asr_candidate",
                    "completed": index + 1,
                    "seconds": round(result["seconds"], 2),
                }
            ),
            flush=True,
        )


def translate(root: Path, output: Path, samples: list[dict], source: str, provider: str) -> None:
    from scripts.run_localization_public_eval import configure_host

    configure_host()
    from camcat.config import get_settings
    from camcat.domain.localization import LocalizationCue
    from camcat.localization.service import TranslationService

    settings = get_settings()
    if provider == "native":
        settings = settings.model_copy(
            update={
                "translation_base_url": settings.narrative_base_url,
                "translation_model": settings.narrative_model,
                "translation_api_key": settings.narrative_api_key,
            }
        )
    service = TranslationService(settings)
    try:
        for index, sample in enumerate(samples):
            identifier = sample["sentence_id"]
            if source == "gold":
                text = sample["source_text"]
            else:
                asr = (
                    root / "checks/quality_repair_20261114/candidates/asr" / (identifier + ".json")
                )
                text = json.loads(asr.read_text())["text"]
                from opencc import OpenCC

                text = OpenCC("t2s").convert(text)
            began = time.monotonic()
            result = {
                "sentence_id": identifier,
                "source": text,
                "model": settings.translation_model,
            }
            try:
                cues = service.translate(
                    [
                        LocalizationCue(
                            cue_id="source-cue",
                            start_us=0,
                            duration_us=60_000_000,
                            source_text=text,
                            text=text,
                        )
                    ],
                    language="en",
                    glossary={},
                )
                result.update(status="succeeded", text=" ".join(c.text for c in cues))
            except Exception as error:
                result.update(status="error", error=type(error).__name__)
            result["seconds"] = time.monotonic() - began
            write_json(output / (identifier + ".json"), result)
            print(
                json.dumps(
                    {"phase": "mt_" + source, "completed": index + 1, "status": result["status"]}
                ),
                flush=True,
            )
    finally:
        service.client.client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["asr", "mt"])
    parser.add_argument("--input", choices=["gold", "asr"], default="gold")
    parser.add_argument("--provider", choices=["native", "deployed"], default="native")
    args = parser.parse_args()
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    manifest = json.loads(
        (root / "benchmarks/localization_public_v1/prepared-inputs.json").read_text()
    )
    kind = "asr" if args.mode == "asr" else "mt_" + args.input
    if args.mode == "mt" and args.provider == "deployed":
        kind = "mt_deployed_" + args.input
    output = root / "checks/quality_repair_20261114/candidates" / kind
    output.mkdir(parents=True, exist_ok=True)
    if args.mode == "asr":
        recognize(root, output, manifest["samples"])
    else:
        translate(root, output, manifest["samples"], args.input, args.provider)
