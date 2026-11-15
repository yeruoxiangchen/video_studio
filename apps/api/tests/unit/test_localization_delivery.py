"""Business delivery invariants; pure state/ZIP unit tests, no provider acceptance."""

import json
import zipfile
from pathlib import Path

import pytest
from camcat.domain.commands import SetLocalizationMetadata, UpdateTextSegment, UpdateTitle
from camcat.domain.localization import collect_cues, validate_translations
from camcat.domain.reducer import reduce_commands
from camcat.localization.plans import SpeechArtifact, dubbing_commands, translation_commands
from test_localization import source_project


def translated():
    project = source_project()
    cues = validate_translations(
        collect_cues(project, track_id="captions"),
        {
            "translations": [{"id": "s1", "text": "Hello."}, {"id": "s2", "text": "Welcome here."}],
        },
    )
    return reduce_commands(
        project,
        translation_commands(
            project,
            cues=cues,
            source_track_id="captions",
            language="en",
            source_version=1,
            job_id="translation",
            glossary={"你好": "Hello"},
        ),
    ).project


def dubbed():
    project = translated()
    cues = collect_cues(project, track_id="localization-subtitles")
    audio = [
        SpeechArtifact(
            cue_id=c.cue_id,
            source_id=f"voice-{i}",
            storage_key=f"generated/{i}.wav",
            duration_us=c.duration_us,
            retention_class="generated",
        )
        for i, c in enumerate(cues)
    ]
    return reduce_commands(
        project,
        dubbing_commands(
            project,
            cues=cues,
            audio=audio,
            language="en",
            source_version=2,
            job_id="dubbing",
            original_audio_volume=0,
        ),
    ).project


def approved():
    from camcat.localization.delivery import approval_metadata

    project = dubbed()
    metadata = approval_metadata(
        project, version=3, reviewer="owner", at="2026-11-15T07:00:00+00:00"
    )
    return reduce_commands(project, [SetLocalizationMetadata(metadata=metadata)]).project


def test_applied_translation_saves_project_glossary_and_source_revision():
    from camcat.localization.plans import validate_translation_state

    project = translated()
    assert project.metadata["localization"]["glossary"] == {"你好": "Hello"}
    validate_translation_state(project)
    changed = reduce_commands(
        project, [UpdateTextSegment(segment_id="s1", text="请核对姓名。")]
    ).project
    with pytest.raises(ValueError, match="重新翻译"):
        validate_translation_state(changed)


def test_review_is_explicit_and_invalidated_by_any_new_version_or_content():
    from camcat.localization.delivery import delivery_status

    project = approved()
    assert delivery_status(project, version=4).state == "ready_to_render"
    assert delivery_status(project, version=5).state == "needs_review"
    changed = reduce_commands(project, [UpdateTitle(title="Updated client brief")]).project
    assert delivery_status(changed, version=4).state == "needs_review"
    target = collect_cues(project, track_id="localization-subtitles")[0]
    changed = reduce_commands(
        project, [UpdateTextSegment(segment_id=target.cue_id, text="Good morning.")]
    ).project
    assert delivery_status(changed, version=5).state == "needs_dubbing"


def test_review_requires_real_dubbing_applied_to_the_current_subtitles():
    from camcat.localization.delivery import approval_metadata

    with pytest.raises(ValueError, match="配音"):
        approval_metadata(translated(), version=2, reviewer="owner", at="2026-11-15T07:00:00+00:00")


def test_source_review_hints_preserve_actual_uncertain_words_and_location():
    from camcat.localization.delivery import source_review_hints

    project = source_project().model_copy(
        update={
            "metadata": {
                "source_segments": [
                    {
                        "filename": "product.mp4",
                        "start_time": 3.0,
                        "end_time": 8.0,
                        "transcript_evidence": {
                            "quality": {
                                "review_recommended": True,
                                "low_confidence_word_count": 1,
                                "review_threshold": 0.6,
                            },
                            "segments": [
                                {
                                    "words": [
                                        {"word": "品牌名", "probability": 0.31},
                                        {"word": "介绍", "probability": 0.98},
                                    ]
                                }
                            ],
                        },
                    }
                ]
            }
        }
    )
    hints = source_review_hints(project)
    assert len(hints) == 1
    assert hints[0].words == ["品牌名"]
    assert hints[0].filename == "product.mp4" and hints[0].start_seconds == 3
    assert not source_review_hints(source_project())


def test_delivery_bundle_contains_consistent_customer_artifacts(tmp_path: Path):
    from camcat.localization.delivery import build_delivery_archive

    video = tmp_path / "unit-video.bin"
    video.write_bytes(b"ZIP unit bytes, not external media verification")
    output = tmp_path / "delivery.zip"
    build_delivery_archive(
        approved(),
        version=4,
        video=video,
        output=output,
        delivery_id="delivery-1",
        rendered_at="2026-11-15T07:01:00+00:00",
        maximum_bytes=1_000_000,
    )
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "video.mp4",
            "source.srt",
            "translation.srt",
            "bilingual.srt",
            "glossary.csv",
            "delivery.json",
        }
        assert archive.read("video.mp4") == video.read_bytes()
        assert "你好。" in archive.read("source.srt").decode()
        assert "Hello." in archive.read("translation.srt").decode()
        assert "你好。\nHello." in archive.read("bilingual.srt").decode()
        manifest = json.loads(archive.read("delivery.json"))
        assert manifest["version"] == 4 and manifest["review_status"] == "confirmed"
        assert "storage_key" not in json.dumps(manifest)
        assert "owner" not in json.dumps(manifest)
    with pytest.raises(ValueError, match="大小"):
        build_delivery_archive(
            approved(),
            version=4,
            video=video,
            output=output,
            delivery_id="delivery-1",
            rendered_at="2026-11-15T07:01:00+00:00",
            maximum_bytes=2,
        )


def test_delivery_requires_a_verified_render_not_only_a_success_flag():
    from uuid import uuid4

    from camcat.localization.delivery import delivery_status
    from camcat.schemas import JobResponse

    job = JobResponse(
        job_id=uuid4(),
        kind="render",
        status="succeeded",
        progress=1,
        created_at="2026-11-15T07:00:00+00:00",
    )
    assert (
        delivery_status(approved(), version=4, render_job=job, render_version=4).state == "failed"
    )


def test_download_cleans_up_temporary_files_on_connection_failure(tmp_path: Path):
    import asyncio
    from tempfile import TemporaryDirectory

    from camcat.localization.delivery_files import DeliveryFileResponse

    temporary = TemporaryDirectory(dir=tmp_path)
    folder = Path(temporary.name)
    archive = folder / "delivery.zip"
    archive.write_bytes(b"unit ZIP transport bytes")
    response = DeliveryFileResponse(temporary=temporary, path=archive, filename="delivery.zip")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        raise RuntimeError("disconnected")

    with pytest.raises(RuntimeError, match="disconnected"):
        asyncio.run(response({"type": "http", "method": "GET", "headers": []}, receive, send))
    assert not folder.exists()
