"""Public evaluation must count failures, retain reference isolation and extract safely."""

import io
import json
import tarfile

import pytest


def test_missing_translations_stay_in_the_scoring_denominator():
    from scripts.localization_eval_metrics import score_translations

    refs = {"a": "A quiet boat crosses the blue lake.", "b": "The train leaves at nine."}
    result = score_translations(refs, {"a": refs["a"]})
    assert result["total"] == 2
    assert result["nonempty"] == 1
    assert result["missing"] == 1
    assert 0 < result["chrf_pp"] < 100
    assert "nw:2" in result["chrf_signature"]
    with pytest.raises(ValueError, match="unknown"):
        score_translations(refs, {"outside": "Unexpected sample"})


def test_speech_metrics_count_deleted_samples_and_normalize_format_only():
    from scripts.localization_eval_metrics import normalize_text, score_speech

    assert normalize_text("  ２０２６年，不要丢失数字！ ", characters=True) == "2026年不要丢失数字"
    assert normalize_text("Do not omit 2026!", characters=False) == "do not omit 2026"
    result = score_speech({"a": "甲乙", "b": "丙丁"}, {"a": "甲乙"}, characters=True)
    assert result["total"] == 2 and result["missing"] == 1
    assert result["error_rate"] == 0.5


def test_selected_audio_extraction_rejects_unsafe_and_missing_members(tmp_path):
    from scripts.localization_eval_metrics import extract_selected_audio

    archive = tmp_path / "speech.tar.gz"
    with tarfile.open(archive, "w:gz") as package:
        for name in ["test/one.wav", "test/two.wav"]:
            record = tarfile.TarInfo(name)
            record.size = 4
            package.addfile(record, io.BytesIO(b"RIFF"))
    out = tmp_path / "selected"
    extract_selected_audio(archive, {"one.wav"}, out)
    assert (out / "one.wav").read_bytes() == b"RIFF"
    assert not (out / "two.wav").exists()
    with pytest.raises(ValueError, match="missing"):
        extract_selected_audio(archive, {"missing.wav"}, tmp_path / "missing")
    with tarfile.open(archive, "w:gz") as package:
        record = tarfile.TarInfo("../one.wav")
        record.size = 4
        package.addfile(record, io.BytesIO(b"RIFF"))
    with pytest.raises(ValueError, match="unsafe"):
        extract_selected_audio(archive, {"one.wav"}, tmp_path / "unsafe")


def test_quality_summary_preserves_known_public_failures_and_manual_review():
    from scripts.localization_eval_metrics import suite_summary

    result = suite_summary(
        [{"name": "contracts", "passed": True}],
        {"tasks": 2, "full_reward": 1, "execution_errors": 0},
    )
    assert result["automated_functional_pass"] is True
    assert result["public_editing_quality_pass"] is False
    assert result["manual_review_pending"] is True


def test_actual_srt_validation_checks_text_count_and_times():
    from scripts.localization_eval_metrics import validate_srt

    cue = {"start_us": 0, "duration_us": 2_000_000, "text": "Hello world."}
    text = "1\n00:00:00,000 --> 00:00:02,000\nHello world.\n"
    validate_srt(text, [cue])
    with pytest.raises(ValueError, match="timing"):
        validate_srt(text, [{**cue, "duration_us": 3_000_000}])
    with pytest.raises(ValueError, match="content"):
        validate_srt(text, [{**cue, "text": "Different words."}])


def test_monitor_distinguishes_a_running_stage_from_a_failed_stage(tmp_path):
    import json

    from scripts.run_full_validation import status

    report = tmp_path / "benchmarks/runs/full_validation/latest.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                "state": "running",
                "phase": "public_localization",
                "output": str(report.parent),
                "stages": [
                    {"name": "prepare_public_audio", "passed": True, "exit_code": 0},
                    {"name": "public_localization", "passed": False},
                ],
            }
        )
    )
    stages = status(tmp_path)["stages"]
    assert stages[0]["passed"] is True
    assert stages[1]["state"] == "running"
    assert "passed" not in stages[1]
    (report.parent / "review-findings.json").write_text(
        json.dumps({"failures": 1, "manual_listening_pending": True})
    )
    assert status(tmp_path)["review_findings"]["failures"] == 1


def test_soak_summary_uses_its_real_pointer_and_checks_the_exact_child(tmp_path):
    import json

    from scripts.run_full_validation import reliability_result

    folder = tmp_path / "checks/reliability"
    output = folder / "soak-this-run"
    output.mkdir(parents=True)
    marker = {
        "pid": 42,
        "run_id": "this-run",
        "mode": "soak",
        "state": "completed",
        "output": str(output),
    }
    detail = {
        **marker,
        "health_samples": 111,
        "summary": {"completed_pipelines": 8, "failed_pipelines": 0},
    }
    (folder / "latest-soak.json").write_text(json.dumps(marker))
    # A stale generic file must never supply the result for the current soak child.
    (folder / "latest.json").write_text(json.dumps({"pid": 1, "state": "failed"}))
    (output / "report.json").write_text(json.dumps(detail))
    result = reliability_result(tmp_path, expected_pid=42, exit_code=0)
    assert result["passed"] is True
    assert result["output"] == str(output)
    assert result["summary"]["completed_pipelines"] == 8
    assert reliability_result(tmp_path, expected_pid=43, exit_code=0)["passed"] is False
    assert reliability_result(tmp_path, expected_pid=42, exit_code=1)["passed"] is False
    detail["cleanup_pending_jobs"] = ["unfinished"]
    (output / "report.json").write_text(json.dumps(detail))
    assert reliability_result(tmp_path, expected_pid=42, exit_code=0)["passed"] is False


def test_full_suite_monitor_reads_real_soak_progress(tmp_path):
    import json

    from scripts.run_full_validation import status

    full = tmp_path / "benchmarks/runs/full_validation/latest.json"
    full.parent.mkdir(parents=True)
    full.write_text(
        json.dumps({"state": "running", "phase": "stability_4_clients_10_minutes", "child_pid": 42})
    )
    soak = tmp_path / "checks/reliability/latest-soak.json"
    soak.parent.mkdir(parents=True)
    soak.write_text(json.dumps({"pid": 42, "phase": "soaking", "completed_batches": 2}))
    assert status(tmp_path)["progress"]["completed_batches"] == 2


def test_candidate_protocol_is_explicit_and_keeps_the_original_frozen(tmp_path, monkeypatch):
    from scripts.localization_eval_metrics import evaluation_protocol

    original = tmp_path / "eval/localization_public_pilot_v1.json"
    original.parent.mkdir()
    original.write_text('{"version":"frozen"}')
    monkeypatch.delenv("CAMCAT_VALIDATION_CONFIG", raising=False)
    monkeypatch.setenv("CAMCAT_STUDIO_DATA", str(tmp_path / "runtime"))
    assert evaluation_protocol(tmp_path) == original
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"version":"development-regression"}')
    monkeypatch.setenv("CAMCAT_VALIDATION_CONFIG", str(candidate))
    assert evaluation_protocol(tmp_path) == candidate
    assert original.read_text() == '{"version":"frozen"}'


def test_accepted_runtime_protocol_is_selected_and_its_digest_is_checked(tmp_path, monkeypatch):
    import hashlib

    from scripts.localization_eval_metrics import evaluation_protocol

    data = tmp_path / "runtime"
    selected = tmp_path / "accepted.json"
    selected.write_text('{"version":"accepted"}')
    marker = data / "config/active-validation.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {"protocol": str(selected), "sha256": hashlib.sha256(selected.read_bytes()).hexdigest()}
        )
    )
    monkeypatch.delenv("CAMCAT_VALIDATION_CONFIG", raising=False)
    monkeypatch.setenv("CAMCAT_STUDIO_DATA", str(data))
    assert evaluation_protocol(tmp_path) == selected
    explicit = tmp_path / "explicit.json"
    monkeypatch.setenv("CAMCAT_VALIDATION_CONFIG", str(explicit))
    assert evaluation_protocol(tmp_path) == explicit
    monkeypatch.delenv("CAMCAT_VALIDATION_CONFIG")
    selected.write_text('{"version":"changed"}')
    with pytest.raises(ValueError, match="protocol digest"):
        evaluation_protocol(tmp_path)
