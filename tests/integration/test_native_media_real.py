"""Native sampling against real encoded media; no provider doubles."""

import gc
import subprocess
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from camcat.media.native import sample_frames

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-media")
    source = root / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=25:duration=2",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(source),
        ],
        check=True,
    )
    return source


def test_batch_sampling_keeps_request_order_and_real_timestamps(sample_video):
    requested = [1.2, 0.08, 0.41, 0.41]
    frames = sample_frames(sample_video, requested, width=160)
    assert len(frames) == len(requested)
    for wanted, frame in zip(requested, frames, strict=True):
        assert wanted - 1e-6 <= frame.seconds <= wanted + 0.041
        assert frame.pixels.shape == (90, 160, 3)
        assert frame.pixels.dtype == np.uint8
    assert np.array_equal(frames[2].pixels, frames[3].pixels)
    original = frames[3].pixels.copy()
    frames[2].pixels[:] = 0
    assert np.array_equal(frames[3].pixels, original)
    gc.collect()
    assert frames[0].pixels.sum() > 0


@pytest.mark.parametrize("times", [[-1], [float("nan")], [float("inf")], [0] * 129])
def test_batch_sampling_rejects_invalid_requests(sample_video, times):
    with pytest.raises(ValueError):
        sample_frames(sample_video, times)


def test_sampling_past_video_end_fails_explicitly(sample_video):
    with pytest.raises(RuntimeError, match="frame|end|timestamp"):
        sample_frames(sample_video, [3.0])


def test_concurrent_decoders_have_independent_owned_buffers(sample_video):
    def one(_):
        return sample_frames(sample_video, [0.2, 0.8, 1.4], width=160)

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(one, range(9)))
    reference = runs[0][1].pixels
    assert all(np.array_equal(run[1].pixels, reference) for run in runs)


def test_non_media_input_fails_and_next_decode_works(sample_video, tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_text("invalid media")
    with pytest.raises(RuntimeError):
        sample_frames(broken, [0.1])
    assert sample_frames(sample_video, [0.1])[0].pixels.shape[2] == 3


def test_batch_helper_encodes_all_analysis_frames(sample_video, tmp_path):
    from camcat.media.ffmpeg import extract_analysis_frames
    from PIL import Image

    targets = [tmp_path / f"batch-{i}.jpg" for i in range(3)]
    times = [0.1, 0.9, 1.5]
    observed = extract_analysis_frames(
        sample_video, targets, times=times, width=160, backend="native"
    )
    assert len(observed) == 3
    for target, requested, actual in zip(targets, times, observed, strict=True):
        assert requested <= actual + 1e-6 <= requested + 0.041
        with Image.open(target) as image:
            assert image.size == (160, 90)


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_rotation_matches_the_existing_ffmpeg_path(sample_video, tmp_path, angle):
    rotated = tmp_path / "rotated.mp4"
    options = subprocess.run(["ffmpeg", "-h", "full"], capture_output=True, text=True, check=True)
    rotation = (
        ["-display_rotation:v:0", str(angle)] if "-display_rotation" in options.stdout else []
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            *rotation,
            "-i",
            str(sample_video),
            "-c",
            "copy",
            "-metadata:s:v:0",
            f"rotate={angle}",
            "-y",
            str(rotated),
        ],
        check=True,
    )
    metadata = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream_side_data=rotation",
            "-of",
            "json",
            str(rotated),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"rotation"' in metadata.stdout
    baseline = tmp_path / "reference.rgb"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            "0.4",
            "-i",
            str(rotated),
            "-frames:v",
            "1",
            "-vf",
            "scale=160:-2",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-y",
            str(baseline),
        ],
        check=True,
    )
    actual = sample_frames(rotated, [0.4], width=160)[0].pixels
    reference = np.frombuffer(baseline.read_bytes(), dtype=np.uint8).reshape(actual.shape)
    assert actual.shape == ((90, 160, 3) if angle == 180 else (284, 160, 3))
    assert np.abs(actual.astype(float) - reference).mean() < 3


def test_variable_frame_rate_uses_timestamps(sample_video, tmp_path):
    source = tmp_path / "vfr.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(sample_video),
            "-vf",
            "select=not(mod(n\\,7))",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-y",
            str(source),
        ],
        check=True,
    )
    frames = sample_frames(source, [0.1, 0.4, 1.1], width=160)
    assert [frame.seconds for frame in frames] == pytest.approx([0.28, 0.56, 1.12], abs=0.001)


def test_batch_file_helper_bounds_work_in_chunks(sample_video, tmp_path):
    from camcat.media.ffmpeg import extract_analysis_frames

    paths = [tmp_path / f"sample-{i}.jpg" for i in range(129)]
    times = [0.2] * len(paths)
    actual = extract_analysis_frames(sample_video, paths, times=times, width=160)
    assert len(actual) == 129
    assert all(path.is_file() for path in paths)


def test_sparse_index_seeks_and_nonzero_start_times(sample_video, tmp_path):
    source = tmp_path / "offset.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-stream_loop",
            "3",
            "-i",
            str(sample_video),
            "-frames:v",
            "175",
            "-vf",
            "setpts=PTS+2/TB",
            "-c:v",
            "libx264",
            "-g",
            "25",
            "-threads",
            "2",
            "-y",
            str(source),
        ],
        check=True,
    )
    metadata = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=start_time",
            "-of",
            "csv=p=0",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert float(metadata.stdout.strip()) == pytest.approx(2)
    times = [6.24, 0.12, 5.4, 0.8]
    frames = sample_frames(source, times, width=160)
    assert [frame.seconds for frame in frames] == pytest.approx(times, abs=0.001)
