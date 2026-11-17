"""Compare actual legacy CLI and C++ sampling, including JPEG writes, on one input."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from camcat.media.ffmpeg import extract_analysis_frames, probe
from PIL import Image


def owned_rss(pid: int) -> int:
    """Sample only this benchmark subprocess and its descendants, in KiB."""
    try:
        fields = Path(f"/proc/{pid}/status").read_text().splitlines()
        rss = next((int(row.split()[1]) for row in fields if row.startswith("VmRSS:")), 0)
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        return rss + sum(owned_rss(int(child)) for child in children)
    except (OSError, ValueError):
        return 0


def child(video: Path, out: Path, backend: str, frames: int) -> None:
    metadata = probe(video)
    times = [round((i + 0.5) * min(metadata.video_duration, 30) / frames, 4) for i in range(frames)]
    started = time.perf_counter()
    extract_analysis_frames(
        video, [out / f"{i:03}.jpg" for i in range(frames)], times=times, backend=backend
    )
    elapsed = time.perf_counter() - started
    cpu = resource.getrusage(resource.RUSAGE_SELF)
    descendants = resource.getrusage(resource.RUSAGE_CHILDREN)
    (out / "result.json").write_text(
        json.dumps(
            {
                "backend": backend,
                "seconds": elapsed,
                "frames_per_second": frames / elapsed,
                "cpu_seconds_self_and_children": cpu.ru_utime
                + cpu.ru_stime
                + descendants.ru_utime
                + descendants.ru_stime,
                "self_peak_rss_mib": cpu.ru_maxrss / 1024,
                "largest_child_peak_rss_mib": descendants.ru_maxrss / 1024,
            },
            indent=2,
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=12, choices=range(2, 49))
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--child-backend", choices=["native", "ffmpeg"])
    args = parser.parse_args()
    if args.child_backend:
        child(args.video, args.output, args.child_backend, args.frames)
        return
    args.output.mkdir(parents=True, exist_ok=False)
    measurements = []
    # One explicit warmup per path; measured processes still include their own decoder setup.
    for backend in ("ffmpeg", "native"):
        child(args.video, args.output / ("warmup-" + backend), backend, 2)
    for repeat in range(args.repeats):
        order = ("ffmpeg", "native") if repeat % 2 == 0 else ("native", "ffmpeg")
        for backend in order:
            out = args.output / f"{repeat}-{backend}"
            out.mkdir()
            command = [
                sys.executable,
                "-B",
                str(Path(__file__).resolve()),
                "--video",
                str(args.video),
                "--output",
                str(out),
                "--frames",
                str(args.frames),
                "--child-backend",
                backend,
            ]
            with (out / "process.log").open("w") as log:
                process = subprocess.Popen(
                    command, stdout=log, stderr=subprocess.STDOUT, env=os.environ.copy()
                )
                peak = 0
                deadline = time.monotonic() + 120
                while process.poll() is None:
                    peak = max(peak, owned_rss(process.pid))
                    if time.monotonic() > deadline:
                        process.kill()
                        process.wait()
                        raise TimeoutError("sampling benchmark exceeded two minutes")
                    time.sleep(0.01)
                if process.returncode:
                    raise RuntimeError(f"{backend} benchmark failed; inspect {out / 'process.log'}")
            result = json.loads((out / "result.json").read_text())
            result.update(repeat=repeat, sampled_process_tree_peak_rss_mib=round(peak / 1024, 3))
            measurements.append(result)
    comparison = []
    for i in range(args.frames):
        with Image.open(args.output / "0-ffmpeg" / f"{i:03}.jpg") as image:
            baseline = np.asarray(image).astype(float)
        with Image.open(args.output / "0-native" / f"{i:03}.jpg") as image:
            native = np.asarray(image).astype(float)
        if baseline.shape != native.shape:
            raise ValueError("sampled image dimensions differ")
        comparison.append(float(np.abs(baseline - native).mean()))
    medians = {
        backend: statistics.median(
            row["seconds"] for row in measurements if row["backend"] == backend
        )
        for backend in ("ffmpeg", "native")
    }
    import _camcat_media_native

    with args.video.open("rb") as video_file:
        source_digest = hashlib.file_digest(video_file, "sha256").hexdigest()
    report = {
        "state": "completed",
        "input": str(args.video),
        "input_sha256": source_digest,
        "scope": "Same source, timestamps and 512px width; wall time includes sampling and JPEG "
        "writes, excludes Python startup and metadata probing. CLI uses FFmpeg q=2, native "
        "uses Pillow quality=95. Versions/encoding differ; pixel MAE is reported separately.",
        "frames": args.frames,
        "repeats": args.repeats,
        "median_seconds": medians,
        "latency_reduction_percent": round((1 - medians["native"] / medians["ffmpeg"]) * 100, 2),
        "jpeg_mean_absolute_pixel_difference": statistics.mean(comparison),
        "native_ffmpeg_version": _camcat_media_native.ffmpeg_version,
        "cli_ffmpeg_version": subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0],
        "rss_sampling_interval_seconds": 0.01,
        "measurements": measurements,
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "median_seconds",
                    "latency_reduction_percent",
                    "jpeg_mean_absolute_pixel_difference",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
