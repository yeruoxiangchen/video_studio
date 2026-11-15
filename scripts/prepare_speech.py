"""Prepare this project's own speech model/config, optionally copying known local artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shlex
import shutil
import urllib.request
from pathlib import Path

REVISION = "b145f2ac26522d6a2ccde0164b7b0e48b1e3199c"
VOICE = "en_US-lessac-medium"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(source, target)
        target.chmod(source.stat().st_mode & 0o777)
    if digest(source) != digest(target):
        raise RuntimeError(f"Artifact copy differs: {target.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-from", type=Path)
    args = parser.parse_args()
    root = Path(os.environ["CAMCAT_STUDIO_DATA"])
    model_dir = root / "models" / "piper"
    model_dir.mkdir(parents=True, exist_ok=True)
    receipts = []
    for name in (f"{VOICE}.onnx", f"{VOICE}.onnx.json", "MODEL_CARD"):
        target = model_dir / name
        url = (
            "https://huggingface.co/rhasspy/piper-voices/resolve/"
            f"{REVISION}/en/en_US/lessac/medium/{name}"
        )
        if args.import_from:
            source = args.import_from / "models" / "piper" / name
            revision_file = source.parent / "revision.txt"
            if revision_file.read_text().strip() != REVISION:
                raise RuntimeError("Imported voice revision does not match the pinned source")
            verified_copy(source, target)
        elif not target.exists():
            request = urllib.request.Request(url, headers={"User-Agent": "CamCat-Studio"})
            with (
                urllib.request.urlopen(request, timeout=600) as response,
                target.with_suffix(target.suffix + ".part").open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
            target.with_suffix(target.suffix + ".part").replace(target)
        receipts.append({"name": name, "source": url, "sha256": digest(target)})
    (model_dir / "revision.txt").write_text(REVISION + "\n")
    if args.import_from:
        for name in ("ffmpeg", "ffprobe", "GPLv3.txt", "readme.txt"):
            verified_copy(
                args.import_from / "tools" / "ffmpeg" / name, root / "tools" / "ffmpeg" / name
            )
        for name in ("NotoSansCJKsc-Regular.otf", "LICENSE"):
            verified_copy(
                args.import_from / "tools" / "fonts" / name, root / "tools" / "fonts" / name
            )
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    environment = config / "speech.env"
    if not environment.exists():
        token = secrets.token_urlsafe(32)
        values = {
            "CAMCAT_SPEECH_MODEL_PATH": str(model_dir / f"{VOICE}.onnx"),
            "CAMCAT_SPEECH_API_KEY": token,
            "CAMCAT_SPEECH_VOICE": VOICE,
            "CAMCAT_SPEECH_REVISION": REVISION,
            "CAMCAT_TTS_BASE_URL": "http://127.0.0.1:18961",
            "CAMCAT_TTS_API_KEY": token,
            "CAMCAT_TTS_MODEL": "piper",
            "CAMCAT_TTS_VOICE": VOICE,
            "CAMCAT_TTS_REVISION": REVISION,
        }
        descriptor = os.open(environment, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(
                "".join(f"export {key}={shlex.quote(value)}\n" for key, value in values.items())
            )
    (config / "fonts.conf").write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd">'
        '<fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
        f"<dir>{root / 'tools' / 'fonts'}</dir>"
        f"<cachedir>{root / 'cache' / 'fontconfig'}</cachedir></fontconfig>"
    )
    (model_dir / "receipt.json").write_text(
        json.dumps(
            {
                "model": VOICE,
                "revision": REVISION,
                "artifacts": receipts,
                "engine": "piper-tts==1.3.0",
                "device": "cpu",
                "threads": 2,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        "Own Piper model and private config ready; provenance: " + str(model_dir / "receipt.json")
    )


if __name__ == "__main__":
    main()
