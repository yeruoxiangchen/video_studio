"""Non-integral subtitle windows must be trimmed after resampling to the target clock."""

import subprocess
import wave

import pytest
from camcat.localization.service import normalize_speech

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("sample_rate", [16000, 22050, 44100])
@pytest.mark.parametrize("window_us", [6_366_670, 7_633_330])
def test_subtitle_windows_preserve_the_exact_48khz_sample_count(tmp_path, sample_rate, window_us):
    source = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate={sample_rate}:duration=1",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    output = tmp_path / "aligned.wav"
    normalize_speech(source, output, window_us=window_us, max_speed=1.2)
    with wave.open(str(output)) as audio:
        assert audio.getframerate() == 48000
        assert audio.getnchannels() == 2
        assert audio.getnframes() == (window_us * 48000 + 999999) // 1_000_000


def test_retimed_speech_preserves_non_integral_subtitle_window(tmp_path):
    source = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=22050:duration=7",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    output = tmp_path / "aligned.wav"
    fit = normalize_speech(source, output, window_us=6_366_670, max_speed=1.2)
    assert 1 < fit.speed < 1.2
    with wave.open(str(output)) as audio:
        assert audio.getnframes() == 305601
