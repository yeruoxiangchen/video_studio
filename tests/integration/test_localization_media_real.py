from __future__ import annotations

import io
import json
import os
import subprocess
import wave
from pathlib import Path
from uuid import UUID

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.external]


@pytest.mark.skipif(not os.environ.get("CAMCAT_TTS_BASE_URL"), reason="real TTS endpoint required")
def test_real_speech_endpoint_and_ffmpeg_time_window(tmp_path: Path) -> None:
    from camcat.config import get_settings
    from camcat.localization.service import SpeechClient, audio_duration, normalize_speech

    settings = get_settings()
    natural = tmp_path / "speech.wav"
    SpeechClient(settings).synthesize(
        "Welcome to CamCat. Edit your story and share it with the world.",
        natural,
        voice=settings.tts_voice,
    )
    with wave.open(io.BytesIO(natural.read_bytes())) as audio:
        assert audio.getnframes() > audio.getframerate()
        assert audio.getsampwidth() == 2
    seconds = audio_duration(natural)
    window_us = round(seconds * 1_000_000 / 1.1)
    output = tmp_path / "aligned.wav"
    fit = normalize_speech(natural, output, window_us=window_us, max_speed=1.2)
    assert 1.09 < fit.speed < 1.11
    assert 0 <= audio_duration(output) * 1_000_000 - window_us < 2000
    with wave.open(str(output)) as audio:
        assert audio.getframerate() == 48000
        assert audio.getnchannels() == 2
    with pytest.raises(ValueError, match="时间窗"):
        normalize_speech(natural, tmp_path / "too-short.wav", window_us=100_000, max_speed=1.2)


@pytest.mark.skipif(not os.environ.get("CAMCAT_TTS_BASE_URL"), reason="real TTS endpoint required")
def test_real_speech_endpoint_rejects_bad_identity_and_voice() -> None:
    from camcat.config import get_settings

    settings = get_settings()
    body = {"model": settings.tts_model, "voice": settings.tts_voice, "input": "CamCat."}
    with httpx.Client(base_url=settings.tts_base_url, timeout=30, trust_env=False) as client:
        assert client.post("/v1/audio/speech", json=body).status_code == 401
        body["voice"] = "unavailable-voice"
        result = client.post(
            "/v1/audio/speech",
            json=body,
            headers={
                "Authorization": "Bearer " + settings.tts_api_key.get_secret_value(),
            },
        )
        assert result.status_code == 422


@pytest.mark.skipif(
    not (os.environ.get("CAMCAT_TTS_BASE_URL") and os.environ.get("CAMCAT_TRANSLATION_BASE_URL")),
    reason="real translation and TTS endpoints required",
)
def test_real_speech_renders_through_camcat_v2_timeline(tmp_path: Path) -> None:
    from camcat.config import get_settings
    from camcat.domain.localization import LocalizationCue, collect_cues
    from camcat.domain.project import (
        Canvas,
        EditingProjectV2,
        MediaSourceRef,
        TextSegment,
        TextTrack,
        TimelineV2,
        VideoSegment,
        VideoTrack,
    )
    from camcat.domain.reducer import reduce_commands
    from camcat.localization.plans import SpeechArtifact, dubbing_commands, translation_commands
    from camcat.localization.service import SpeechClient, TranslationService, normalize_speech
    from camcat.rendering.build import RenderBuildService
    from camcat.rendering.doctor import inspect_capabilities
    from camcat.rendering.ffmpeg_renderer import FFmpegRenderer
    from camcat.rendering.fingerprint import fingerprint_media
    from camcat.rendering.verification import verify_output
    from camcat.timeline.compiler import TimelineCompilerV2
    from camcat.timeline.schemas import RenderProfile

    # Diagnostic footage is generated; translations and speech come from real local models.
    # The original upload/Agent and persistent queue journeys have separate real tests.
    settings = get_settings()
    translated_cues = TranslationService(settings).translate(
        [
            LocalizationCue(
                cue_id="c1",
                start_us=0,
                duration_us=4_000_000,
                source_text="欢迎使用 CamCat。",
                text="欢迎使用 CamCat。",
            ),
        ],
        language="en",
        glossary={"CamCat": "CamCat"},
    )
    natural, aligned, video = (
        tmp_path / name for name in ("natural.wav", "aligned.wav", "input.mp4")
    )
    SpeechClient(settings).synthesize(translated_cues[0].text, natural, voice=settings.tts_voice)
    normalize_speech(natural, aligned, window_us=4_000_000, max_speed=1.2)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x142c42:size=640x360:rate=30:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        timeout=60,
    )
    project = EditingProjectV2(
        project_id="media-integration",
        title="CamCat 译制媒体验证",
        goal="Media integration",
        canvas=Canvas(width=640, height=360, fps_num=30, fps_den=1),
        sources=[
            MediaSourceRef(
                source_id="video",
                origin="licensed_library",
                storage_key="diagnostics/input.mp4",
                retention_class="library",
            )
        ],
        timeline=TimelineV2(
            tracks=[
                VideoTrack(
                    track_id="main",
                    name="Video",
                    segments=[
                        VideoSegment(
                            segment_id="video-1",
                            source_id="video",
                            timeline_start_us=0,
                            timeline_duration_us=4_000_000,
                            source_start_us=0,
                            source_duration_us=4_000_000,
                        )
                    ],
                ),
                TextTrack(
                    track_id="captions",
                    name="Original",
                    segments=[
                        TextSegment(
                            segment_id="c1",
                            start_us=0,
                            duration_us=4_000_000,
                            text="欢迎使用 CamCat。",
                        )
                    ],
                ),
            ]
        ),
    )
    translated = reduce_commands(
        project,
        translation_commands(
            project,
            cues=translated_cues,
            source_track_id="captions",
            language="en",
            source_version=1,
            job_id="media-integration",
        ),
    ).project
    cues = collect_cues(translated, track_id="localization-subtitles")
    speech = fingerprint_media(
        aligned, media_id="speech", storage_key="diagnostics/voice.wav", retention_class="generated"
    )
    dubbed = reduce_commands(
        translated,
        dubbing_commands(
            translated,
            cues=cues,
            language="en",
            source_version=2,
            job_id="speech-integration",
            original_audio_volume=0,
            audio=[
                SpeechArtifact(
                    cue_id=cues[0].cue_id,
                    source_id="speech",
                    storage_key="diagnostics/voice.wav",
                    duration_us=speech.ref.duration_us,
                    retention_class="generated",
                    sha256=speech.ref.sha256,
                )
            ],
        ),
    ).project
    sources = {
        "video": fingerprint_media(video, media_id="video", storage_key="diagnostics/input.mp4"),
        "speech": speech,
    }
    profile = RenderProfile()
    timeline = TimelineCompilerV2(profile).compile(
        session_id=UUID(int=1), state_version=3, project=dubbed, sources=sources
    )
    assert timeline.video_tracks[0].segments[0].audio_volume == 0
    assert len(timeline.text_tracks) == 2 and len(timeline.audio_tracks) == 1
    build = RenderBuildService().create(
        tmp_path / "build",
        timeline=timeline,
        profile=profile,
        capability=inspect_capabilities(tmp_path / "runtime"),
    )
    output = tmp_path / "camcat-bilingual-dub.mp4"
    FFmpegRenderer().render(build, sources, output)
    verified = verify_output(build, output)
    assert verified.actual_frames == 120
    assert verified.audio_stream_actual and verified.full_decode_succeeds
    (tmp_path / "verification.json").write_text(
        json.dumps(verified.model_dump(mode="json"), indent=2)
    )
