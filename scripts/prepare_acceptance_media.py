"""Prepare licensed footage with real generated narration for ASR/browser acceptance."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

import httpx
from dotenv import dotenv_values


def main() -> None:
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    folder = root / "runtime/acceptance"
    folder.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1]
    provenance = json.loads((source / "docs/open-video-library.json").read_text())[0]
    url = provenance["download_url"]
    original = folder / provenance["filename"]
    if not original.exists():
        temporary = original.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        temporary.replace(original)
    config = dotenv_values(root / "config/speech.env")
    speech = folder / "narration.wav"
    text = "Welcome to CamCat."
    if not speech.exists():
        response = httpx.post(
            "http://127.0.0.1:18961/v1/audio/speech",
            timeout=120,
            trust_env=False,
            headers={"Authorization": "Bearer " + str(config["CAMCAT_TTS_API_KEY"])},
            json={
                "model": "piper",
                "voice": "en_US-lessac-medium",
                "input": text,
                "response_format": "wav",
            },
        )
        response.raise_for_status()
        speech.write_bytes(response.content)
    duration = min(
        20,
        float(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(original),
                ],
                text=True,
            ).strip()
        ),
    )
    video, image = folder / "source.mp4", folder / "reference.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(original),
            "-i",
            str(speech),
            "-filter_complex",
            f"[1:a]apad,atrim=duration={duration}[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-t",
            str(duration),
            "-vf",
            "scale=640:-2",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        timeout=180,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(video), "-frames:v", "1", str(image)],
        check=True,
        timeout=60,
    )
    (folder / "provenance.json").write_text(
        json.dumps(
            {
                **provenance,
                "derivation": "Short licensed footage plus actual Piper-generated narration",
                "narration": text,
                "voice": "en_US-lessac-medium",
                "duration_seconds": duration,
                "test_role": "user-upload input; licensed library is imported separately",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print("Licensed browser/ASR acceptance input prepared: " + str(video))


if __name__ == "__main__":
    main()
