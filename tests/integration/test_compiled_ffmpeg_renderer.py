from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from uuid import UUID

import pytest
from camcat.domain.project import (
    AudioSegment,
    AudioTrack,
    Canvas,
    EditingProjectV2,
    MediaSourceRef,
    TextSegment,
    TextStyle,
    TextTrack,
    TimelineV2,
    Transform,
    TransitionEdge,
    VideoSegment,
    VideoTrack,
)
from camcat.rendering.build import RenderBuildService
from camcat.rendering.doctor import inspect_capabilities
from camcat.rendering.ffmpeg_renderer import FFmpegRenderer
from camcat.rendering.fingerprint import fingerprint_media
from camcat.rendering.verification import verify_output
from camcat.timeline.compiler import TimelineCompilerV2
from camcat.timeline.schemas import RenderProfile

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="real FFmpeg and FFprobe are required",
    ),
]


def test_long_bilingual_captions_are_visibly_separated_in_real_render(tmp_path: Path) -> None:
    from PIL import Image

    source = tmp_path / "black.mp4"
    _video(source, "black", 440)
    media = fingerprint_media(source, media_id="footage", storage_key="test/black.mp4")
    project = EditingProjectV2(
        project_id="bilingual-layout",
        title="Bilingual layout",
        goal="Readable subtitles",
        canvas=Canvas(width=640, height=360, fps_num=30, fps_den=1),
        sources=[
            MediaSourceRef(
                source_id="footage",
                origin="licensed_library",
                storage_key="test/black.mp4",
                retention_class="library",
            )
        ],
        timeline=TimelineV2(
            tracks=[
                VideoTrack(
                    track_id="main",
                    name="Main",
                    segments=[
                        VideoSegment(
                            segment_id="clip",
                            source_id="footage",
                            timeline_start_us=0,
                            timeline_duration_us=2_000_000,
                            source_start_us=0,
                            source_duration_us=2_000_000,
                        )
                    ],
                ),
                TextTrack(
                    track_id="captions",
                    name="Chinese",
                    segments=[
                        TextSegment(
                            segment_id="source",
                            start_us=0,
                            duration_us=2_000_000,
                            text="游客乘船穿过湖面，欣赏两岸的城市风景。" * 3,
                            style=TextStyle(y=0.78, color="#00FF00"),
                        )
                    ],
                ),
                TextTrack(
                    track_id="localization-subtitles",
                    name="English",
                    segments=[
                        TextSegment(
                            segment_id="translated",
                            start_us=0,
                            duration_us=2_000_000,
                            text=(
                                "The visitors sail across the lake "
                                "and enjoy the city on both banks. "
                            )
                            * 5,
                            style=TextStyle(y=0.90, color="#FFFFFF"),
                        )
                    ],
                ),
            ]
        ),
        metadata={
            "localization": {
                "source_track_id": "captions",
                "target_track_id": "localization-subtitles",
            }
        },
    )
    profile = RenderProfile()
    timeline = TimelineCompilerV2(profile).compile(
        session_id=UUID(int=11), state_version=1, project=project, sources={"footage": media}
    )
    build = RenderBuildService().create(
        tmp_path / "build",
        timeline=timeline,
        profile=profile,
        capability=inspect_capabilities(tmp_path / "runtime"),
    )
    output = tmp_path / "bilingual.mp4"
    FFmpegRenderer().render(build, {"footage": media}, output)
    assert verify_output(build, output).full_decode_succeeds
    for index, at in enumerate((0.1, 0.7, 1.4)):
        frame = tmp_path / f"caption-{index}.png"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(at),
                "-i",
                str(output),
                "-frames:v",
                "1",
                str(frame),
            ],
            check=True,
            timeout=30,
        )
        image = Image.open(frame).convert("RGB")
        green, white = [], []
        for y in range(image.height):
            for x in range(image.width):
                red, g, blue = image.getpixel((x, y))
                if g > 120 and red < 70 and blue < 70:
                    green.append(y)
                if min(red, g, blue) > 170:
                    white.append(y)
        assert green and white, "Both actual subtitle languages must be visible"
        assert max(green) + 4 < min(white), "Bilingual glyph regions overlap"


def _video(path: Path, color: str, tone: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color={color}:size=320x180:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:sample_rate=48000:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


def _audio(path: Path, tone: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:sample_rate=48000:duration=3",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


def test_v2_background_overlay_text_dialogue_bgm_and_dissolve_render_exact_frames(
    tmp_path: Path,
) -> None:
    first, second, overlay, bgm = (
        tmp_path / name for name in ("first.mp4", "second.mp4", "overlay.mp4", "bgm.m4a")
    )
    _video(first, "red", 440)
    _video(second, "blue", 550)
    _video(overlay, "green", 660)
    _audio(bgm, 220)
    sources = {
        name: fingerprint_media(path, media_id=name, storage_key=f"test/{path.name}")
        for name, path in {"first": first, "second": second, "overlay": overlay, "bgm": bgm}.items()
    }
    refs = [
        MediaSourceRef(
            source_id=name,
            origin="licensed_library",
            storage_key=f"test/{item.local_path.name}",
            retention_class="library",
        )
        for name, item in sources.items()
    ]
    project = EditingProjectV2(
        project_id="p",
        goal="integration",
        title="V2",
        canvas=Canvas(width=320, height=180, fps_num=30, fps_den=1),
        sources=refs,
        timeline=TimelineV2(
            tracks=[
                VideoTrack(
                    track_id="main",
                    name="Main",
                    segments=[
                        VideoSegment(
                            segment_id="one",
                            source_id="first",
                            timeline_start_us=0,
                            timeline_duration_us=1_000_000,
                            source_start_us=300_000,
                            source_duration_us=1_000_000,
                        ),
                        VideoSegment(
                            segment_id="two",
                            source_id="second",
                            timeline_start_us=1_000_000,
                            timeline_duration_us=1_000_000,
                            source_start_us=300_000,
                            source_duration_us=1_000_000,
                        ),
                    ],
                ),
                VideoTrack(
                    track_id="overlay-track",
                    name="Overlay",
                    segments=[
                        VideoSegment(
                            segment_id="overlay-segment",
                            source_id="overlay",
                            timeline_start_us=400_000,
                            timeline_duration_us=800_000,
                            source_start_us=300_000,
                            source_duration_us=800_000,
                            transform=Transform(
                                x=0.2, y=-0.2, scale_x=0.35, scale_y=0.35, opacity=0.75
                            ),
                        )
                    ],
                ),
                TextTrack(
                    track_id="captions",
                    name="Captions",
                    segments=[
                        TextSegment(
                            segment_id="caption",
                            start_us=400_000,
                            duration_us=800_000,
                            text="CamCat V2",
                        )
                    ],
                ),
                AudioTrack(
                    track_id="dialogue",
                    name="Dialogue",
                    role="dialogue",
                    segments=[
                        AudioSegment(
                            segment_id="dialogue-one",
                            source_id="first",
                            timeline_start_us=0,
                            timeline_duration_us=1_000_000,
                            source_start_us=300_000,
                            source_duration_us=1_000_000,
                        )
                    ],
                ),
                AudioTrack(
                    track_id="music",
                    name="BGM",
                    role="bgm",
                    segments=[
                        AudioSegment(
                            segment_id="music-one",
                            source_id="bgm",
                            timeline_start_us=0,
                            timeline_duration_us=2_000_000,
                            source_start_us=0,
                            source_duration_us=2_000_000,
                            volume=0.08,
                        )
                    ],
                ),
            ],
            transitions=[
                TransitionEdge(
                    transition_id="fade",
                    left_segment_id="one",
                    right_segment_id="two",
                    type="dissolve",
                    duration_us=200_000,
                )
            ],
        ),
    )
    profile = RenderProfile()
    timeline = TimelineCompilerV2(profile).compile(
        session_id=UUID(int=1), state_version=7, project=project, sources=sources
    )
    assert timeline.frame_count == 60
    build = RenderBuildService().create(
        tmp_path / "build",
        timeline=timeline,
        profile=profile,
        capability=inspect_capabilities(tmp_path / "runtime"),
    )
    output = tmp_path / "render.mp4"
    FFmpegRenderer().render(build, sources, output)
    result = verify_output(build, output)
    assert result.actual_frames == result.expected_frames == 60
    assert (result.width, result.height) == (320, 180)
    assert result.audio_stream_actual and result.full_decode_succeeds and result.output_sha256


def test_video_tail_shorter_than_container_renders_the_compiled_frame_count(tmp_path: Path) -> None:
    source = tmp_path / "audio-tail.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:size=320x180:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2.06",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    sources = {"source": fingerprint_media(source, media_id="source", storage_key="test/tail.mp4")}
    project = EditingProjectV2(
        project_id="tail",
        goal="full-length source cut",
        title="Tail",
        canvas=Canvas(width=320, height=180, fps_num=30, fps_den=1),
        sources=[
            MediaSourceRef(
                source_id="source",
                origin="user_upload",
                storage_key="test/tail.mp4",
                retention_class="transient_4h",
            )
        ],
        timeline=TimelineV2(
            tracks=[
                VideoTrack(
                    track_id="main",
                    name="Main",
                    segments=[
                        VideoSegment(
                            segment_id="tail-cut",
                            source_id="source",
                            timeline_start_us=0,
                            timeline_duration_us=2_000_000,
                            source_start_us=50_000,
                            source_duration_us=2_000_000,
                        )
                    ],
                )
            ]
        ),
    )
    profile = RenderProfile()
    timeline = TimelineCompilerV2(profile).compile(
        session_id=UUID(int=2), state_version=1, project=project, sources=sources
    )
    build = RenderBuildService().create(
        tmp_path / "build",
        timeline=timeline,
        profile=profile,
        capability=inspect_capabilities(tmp_path / "runtime"),
    )
    output = tmp_path / "tail-render.mp4"
    FFmpegRenderer().render(build, sources, output)
    verification = verify_output(build, output)
    assert verification.expected_frames == verification.actual_frames == 60
    assert verification.full_decode_succeeds and verification.audio_stream_actual
