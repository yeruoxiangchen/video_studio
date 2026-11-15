"""Actual FFmpeg regression for a longer audio tail than the visible video."""

import subprocess

import pytest
from camcat.agent.narrative import narrative_sample_times
from camcat.media.ffmpeg import extract_analysis_frame, probe

pytestmark = pytest.mark.integration


def test_narrative_sampling_uses_the_decodable_video_duration(tmp_path):
    source = tmp_path / "audio-tail.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-c:a",
            "aac",
            "-y",
            str(source),
        ],
        check=True,
    )
    metadata = probe(source)
    assert metadata.duration > 1.5
    assert 0.95 <= metadata.video_duration <= 1.05
    for index, at in enumerate(narrative_sample_times(metadata.video_duration)):
        target = tmp_path / f"frame-{index}.jpg"
        extract_analysis_frame(source, target, at=at)
        assert target.is_file() and target.stat().st_size > 0
