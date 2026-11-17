from copy import deepcopy

import pytest

from scripts.measure_full_pipeline import summarize_acceptance


@pytest.fixture
def acceptance():
    names = [
        "upload_start",
        "analysis_done",
        "plan_start",
        "plan_done",
        "localization_start",
        "localization_done",
        "render_start",
        "render_done",
        "downloaded",
    ]
    jobs = [
        {
            "job_id": kind,
            "kind": kind,
            "status": "succeeded",
            "attempts": 1,
            "created_at": "2026-11-17T00:00:00Z",
            "started_at": "2026-11-17T00:00:01Z",
            "finished_at": "2026-11-17T00:00:03Z",
            "result": {},
        }
        for kind in ["analyze_source", "translate_subtitles", "dub_subtitles", "render"]
    ]
    jobs[0]["result"] = {"frame_backend": "native"}
    jobs[-1]["result"] = {
        "full_decode_succeeds": True,
        "frame_delta": 0,
        "expected_frames": 900,
        "actual_frames": 900,
    }
    return {
        "state": "passed",
        "markers": [{"name": n, "seconds": 10 + i * 5} for i, n in enumerate(names)],
        "jobs": jobs,
        "graph": {"status": "succeeded", "node_trace": []},
    }


def test_full_pipeline_uses_observed_wall_clock_and_actual_job_lifetimes(acceptance):
    result = summarize_acceptance(acceptance)
    assert result["upload_to_download_seconds"] == 40
    assert result["phases_seconds"]["source_upload_and_analysis"] == 5
    assert result["jobs"][0]["queue_seconds"] == 1
    assert result["jobs"][0]["execution_seconds"] == 2
    assert result["frame_backend"] == "native"


@pytest.mark.parametrize(
    "problem",
    [
        "missing_analysis",
        "failed_job",
        "missing_graph",
        "bad_clock",
        "missing_decode",
        "wrong_backend",
    ],
)
def test_incomplete_flows_do_not_produce_a_successful_performance_report(acceptance, problem):
    packet = deepcopy(acceptance)
    if problem == "missing_analysis":
        packet["markers"] = packet["markers"][1:]
    elif problem == "failed_job":
        packet["jobs"][1]["status"] = "failed"
    elif problem == "missing_graph":
        packet["graph"] = {}
    elif problem == "bad_clock":
        packet["markers"][-1]["seconds"] = 1
    elif problem == "missing_decode":
        packet["jobs"][-1]["result"]["full_decode_succeeds"] = False
    else:
        packet["jobs"][0]["result"]["frame_backend"] = "ffmpeg"
    with pytest.raises(ValueError):
        summarize_acceptance(packet)
