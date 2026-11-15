from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from camcat.models import Job, JobKind
from camcat.services.providers import SourceClipSemantics
from camcat.worker import Worker


def worker_and_job():
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(llm_model="unit-visual-model")
    worker.visual_analysis = Mock()
    worker.visual_analysis.analyze_video.return_value = SourceClipSemantics(
        description="A person opens a door", scene="indoor", composition="medium shot", risk_score=0
    )
    job = Job(
        id=uuid4(),
        kind=JobKind.ANALYZE_SOURCE,
        owner_id="unit",
        progress=0.01,
        payload={},
        checkpoint={},
    )
    jobs = Mock()

    def update(_job, _progress, *, checkpoint):
        _job.checkpoint = checkpoint

    jobs.update_progress.side_effect = update
    return worker, job, jobs


def test_a_retry_reuses_completed_visual_analysis_instead_of_replaying_the_batch(tmp_path: Path):
    worker, job, jobs = worker_and_job()
    first, second = tmp_path / "one.mp4", tmp_path / "two.mp4"
    first.write_bytes(b"unit source one")
    second.write_bytes(b"unit source two")
    value = worker.visual_analysis.analyze_video.return_value
    worker.visual_analysis.analyze_video.side_effect = [
        value,
        RuntimeError("provider interrupted"),
        value,
    ]
    worker._source_visual(job, jobs, first, "one")
    with pytest.raises(RuntimeError, match="provider interrupted"):
        worker._source_visual(job, jobs, second, "two")
    worker._source_visual(job, jobs, first, "one")
    worker._source_visual(job, jobs, second, "two")
    assert [c.args[0].name for c in worker.visual_analysis.analyze_video.call_args_list] == [
        "one.mp4",
        "two.mp4",
        "two.mp4",
    ]
    assert all(c.kwargs["compact"] for c in worker.visual_analysis.analyze_video.call_args_list)


@pytest.mark.parametrize("change", ["content", "model"])
def test_changed_source_or_model_invalidates_the_visual_cache(tmp_path: Path, change):
    worker, job, jobs = worker_and_job()
    source = tmp_path / "one.mp4"
    source.write_bytes(b"old input")
    worker._source_visual(job, jobs, source, "one")
    if change == "content":
        source.write_bytes(b"new input")
    else:
        worker.settings.llm_model = "another-unit-model"
    worker._source_visual(job, jobs, source, "one")
    assert worker.visual_analysis.analyze_video.call_count == 2


def test_changing_the_separate_visual_model_invalidates_its_cache(tmp_path: Path):
    worker, job, jobs = worker_and_job()
    worker.settings.visual_analysis_model = "visual-a"
    source = tmp_path / "one.mp4"
    source.write_bytes(b"unit source")
    worker._source_visual(job, jobs, source, "one")
    worker.settings.visual_analysis_model = "visual-b"
    worker._source_visual(job, jobs, source, "one")
    assert worker.visual_analysis.analyze_video.call_count == 2


def test_a_planner_change_retains_completed_work_from_the_same_explicit_visual_model(
    tmp_path: Path,
):
    worker, job, jobs = worker_and_job()
    worker.settings.visual_analysis_model = "explicit-visual"
    source = tmp_path / "one.mp4"
    source.write_bytes(b"unit source")
    worker._source_visual(job, jobs, source, "one")
    worker.settings.llm_model = "new-planner"
    worker._source_visual(job, jobs, source, "one")
    assert worker.visual_analysis.analyze_video.call_count == 1
