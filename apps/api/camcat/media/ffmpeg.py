from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaCommandError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    duration: float
    width: int
    height: int
    has_audio: bool
    video_duration: float = 0.0


def shot_signature(thumbnail: Path) -> str:
    return hashlib.sha256(thumbnail.read_bytes()).hexdigest()[:24]


def measure_visual_quality(path: Path, *, start: float = 0.0, end: float | None = None) -> float:
    """Return a bounded, real-media quality signal using FFmpeg's blur detector."""
    result = _run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            *_input_window(path, start=start, end=end),
            "-vf",
            "scale=320:-2,blurdetect=block_width=32:block_height=32:block_pct=80",
            "-an",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    values = [float(value) for value in re.findall(r"blur mean:\s*([0-9.]+)", result.stderr)]
    if not values:
        return 0.6
    # blurdetect is higher for blurrier frames; map it conservatively into 0..1.
    blur = sum(values) / len(values)
    return round(max(0.05, min(1.0, 1.0 - blur / 20.0)), 4)


def probe(path: Path) -> MediaMetadata:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if video is None:
        raise MediaCommandError("uploaded media has no video stream")
    duration = float(payload.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise MediaCommandError("uploaded media duration is unavailable")
    try:
        visual_duration = float(video.get("duration") or duration)
    except (TypeError, ValueError):
        visual_duration = duration
    if not math.isfinite(visual_duration) or visual_duration <= 0:
        visual_duration = duration
    return MediaMetadata(
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
        video_duration=min(duration, visual_duration),
    )


def detect_scene_cuts(path: Path, threshold: float = 0.35) -> list[float]:
    result = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-filter:v",
            f"select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise MediaCommandError(result.stderr[-2000:])
    return sorted({float(value) for value in re.findall(r"pts_time:([0-9.]+)", result.stderr)})


def extract_clip(source: Path, target: Path, *, start: float, end: float) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(target),
        ]
    )


def extract_thumbnail(source: Path, target: Path, *, at: float) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-ss",
            f"{at:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale=640:-2",
            str(target),
        ]
    )


def extract_analysis_frame(source: Path, target: Path, *, at: float) -> None:
    """Keep analysis sampling identical on host and Compose without encoding a video copy."""
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-threads",
            "2",
            "-ss",
            f"{at:.4f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale=512:-2",
            "-q:v",
            "2",
            "-y",
            str(target),
        ]
    )


def extract_audio(
    source: Path, target: Path, *, start: float = 0.0, end: float | None = None
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-hide_banner",
            *_input_window(source, start=start, end=end),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "mp3",
            str(target),
        ]
    )


def extract_analysis_frames(
    source: Path,
    targets: list[Path],
    *,
    times: list[float],
    width: int = 512,
    backend: str = "native",
) -> list[float]:
    """Extract a bounded batch with an explicitly selected decoder backend."""
    if len(targets) != len(times):
        raise ValueError("frame target and timestamp counts differ")
    if backend == "ffmpeg":
        if width != 512:
            raise ValueError("legacy analysis sampling uses width 512")
        for target, at in zip(targets, times, strict=True):
            extract_analysis_frame(source, target, at=at)
        return times
    if backend != "native":
        raise ValueError("unknown frame sampling backend")
    from PIL import Image

    from camcat.media.native import sample_frames

    observed: list[float] = []
    for offset in range(0, len(times), 16):
        try:
            frames = sample_frames(source, times[offset : offset + 16], width=width)
        except (RuntimeError, ValueError) as error:
            raise MediaCommandError(str(error)) from error
        for target, frame in zip(targets[offset : offset + 16], frames, strict=True):
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(frame.pixels).save(target, format="JPEG", quality=95, subsampling=0)
            observed.append(frame.seconds)
    return observed


def _input_window(source: Path, *, start: float, end: float | None) -> list[str]:
    if start < 0 or (end is not None and end <= start):
        raise ValueError("media window must have nonnegative start and end > start")
    args = ["-ss", f"{start:.6f}"]
    if end is not None:
        args.extend(["-t", f"{end - start:.6f}"])
    return [*args, "-i", str(source)]


def _run(
    args: list[str], *, check: bool = True, timeout_seconds: float = 300.0
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaCommandError(
            f"command {args[0]} timed out after {timeout_seconds:g} seconds"
        ) from exc
    if check and result.returncode != 0:
        raise MediaCommandError(
            f"command {args[0]} failed ({result.returncode}): {result.stderr[-4000:]}"
        )
    return result
