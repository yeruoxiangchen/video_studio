from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from camcat import worker as worker_module
from camcat.media.ffmpeg import MediaMetadata
from camcat.models import Job, JobKind
from camcat.services.providers import SegmentSemantics
from camcat.worker import Worker


@pytest.mark.parametrize("visual_override", [None, "unit-separate-visual"])
@pytest.mark.parametrize("backend", ["ffmpeg", "native"])
def test_source_analysis_reads_original_windows_and_reports_monotonic_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, visual_override: str | None, backend: str
) -> None:
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(
        runtime_dir=str(tmp_path),
        segment_window_seconds=5.0,
        segment_minimum_seconds=3.0,
        llm_model="unit-visual-model",
        visual_analysis_model=visual_override,
        media_frame_backend=backend,
    )
    worker.object_store = Mock()

    def download(_key, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"unit-source-input")

    worker.object_store.download_file.side_effect = download
    worker.visual_analysis = Mock()
    worker.visual_analysis.analyze_video.return_value = SegmentSemantics(
        description="A person opens a blue door",
        scene="indoor",
        composition="medium shot",
        risk_score=0,
    )
    worker.asr = Mock()
    worker.asr.transcribe.return_value = {
        "text": "speech",
        "segments": [{"start": 0, "end": 1, "text": "speech"}],
        "model": "actual-asr-model",
        "quality": {"word_count": 1, "review_recommended": True},
    }
    monkeypatch.setattr(worker_module, "probe", lambda _: MediaMetadata(10, 1280, 720, True))
    monkeypatch.setattr(worker_module, "detect_scene_cuts", lambda _: [])
    monkeypatch.setattr(worker_module, "shot_signature", lambda _: "signature")
    clip = Mock(side_effect=AssertionError("source analysis must not encode disposable clips"))
    audio, thumbnail, quality = Mock(), Mock(), Mock(return_value=0.8)
    monkeypatch.setattr(worker_module, "extract_clip", clip)
    monkeypatch.setattr(worker_module, "extract_audio", audio)
    monkeypatch.setattr(worker_module, "extract_thumbnail", thumbnail)
    frames = Mock()
    monkeypatch.setattr(worker_module, "extract_analysis_frames", frames)
    monkeypatch.setattr(worker_module, "measure_visual_quality", quality)
    job = Job(
        id=uuid4(),
        kind=JobKind.ANALYZE_SOURCE,
        owner_id="unit",
        progress=0,
        payload={
            "batch_id": "batch",
            "expires_at": "2030-01-01T00:00:00Z",
            "source_media": [
                {"media_id": "one", "filename": "one.mp4", "storage_key": "one"},
                {"media_id": "two", "filename": "two.mp4", "storage_key": "two"},
            ],
        },
    )
    jobs = Mock()

    def update_progress(item, progress, *, checkpoint=None):
        item.progress = progress
        if checkpoint is not None:
            item.checkpoint = checkpoint

    jobs.update_progress.side_effect = update_progress
    result = worker.analyze_source(job, jobs)
    assert result["frame_backend"] == backend

    clip.assert_not_called()
    source_dir = tmp_path / "jobs" / str(job.id) / "sources"
    assert [(call.args[0], call.kwargs) for call in audio.call_args_list] == [
        (source_dir / f"{name}.mp4", {"start": start, "end": start + 5})
        for name in ("one", "two")
        for start in (0, 5)
    ]
    if backend == "ffmpeg":
        assert [call.kwargs["at"] for call in thumbnail.call_args_list] == [2.5, 7.5, 2.5, 7.5]
        frames.assert_not_called()
    else:
        thumbnail.assert_not_called()
        assert [call.kwargs["times"] for call in frames.call_args_list] == [[2.5, 7.5]] * 2
    assert [(call.args[0], call.kwargs) for call in quality.call_args_list] == [
        (call.args[0], call.kwargs) for call in audio.call_args_list
    ]
    assert [(item["start_time"], item["end_time"]) for item in result["source_segments"]] == [
        (0, 5),
        (5, 10),
        (0, 5),
        (5, 10),
    ]
    assert all(item["transcript_cues"][0]["start"] == 0 for item in result["source_segments"])
    assert all(
        item["transcript_evidence"]["quality"]["review_recommended"]
        and item["transcript_evidence"]["model"] == "actual-asr-model"
        for item in result["source_segments"]
    )
    progress = [call.args[1] for call in jobs.update_progress.call_args_list]
    assert progress == sorted(progress)
    assert progress[-1] == pytest.approx(0.95)
    stages = [call.args[1] for call in jobs.checkpoint.call_args_list]
    assert stages.count("source_segment_started") == 4
    assert stages.count("source_audio_ready") == 4
    assert worker.visual_analysis.analyze_video.call_count == 2
    for media in result["source_media"]:
        assert media["visual_semantics"]["description"] == "A person opens a blue door"
        assert media["visual_evidence"] == {
            "media_id": media["media_id"],
            "start_time": 0,
            "end_time": 10,
            "scope": "whole_media",
            "model": visual_override or "unit-visual-model",
        }
