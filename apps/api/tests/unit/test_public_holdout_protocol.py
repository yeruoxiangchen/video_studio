"""New public inputs have their own frozen protocol and result provenance."""

import json
import signal
from copy import deepcopy

import pytest

from scripts.public_benchmark_protocol import SOURCE, BenchmarkRun, sha256, verify_receipts


def protocols():
    return (
        json.loads((SOURCE / "eval/public_holdout_v1.json").read_text()),
        json.loads((SOURCE / "eval/public_pilot_v1.json").read_text()),
    )


def test_holdout_selects_new_tasks_without_replacing_the_frozen_pilot():
    from scripts.run_public_holdout import validate_protocol

    holdout, pilot = protocols()
    validate_protocol(holdout, pilot)
    assert {t["id"] for t in holdout["agentic"]["tasks"]}.isdisjoint(
        t["id"] for t in pilot["agentic"]["tasks"]
    )
    assert sha256(SOURCE / "eval/public_pilot_v1.json") == (
        "2d3dd0df0a59444e3cbd9664e9921d80be57559f3d9ff48f2733b3f00eaee8db"
    )


@pytest.mark.parametrize("change", ["overlap", "duplicate", "attempts", "revision", "archive"])
def test_holdout_rejects_contamination_and_unfrozen_execution(change):
    from scripts.run_public_holdout import validate_protocol

    holdout, pilot = protocols()
    holdout = deepcopy(holdout)
    spec = holdout["agentic"]
    if change == "overlap":
        spec["tasks"][0] = deepcopy(pilot["agentic"]["tasks"][0])
    elif change == "duplicate":
        spec["tasks"][1] = deepcopy(spec["tasks"][0])
    elif change == "attempts":
        spec["task_attempts"] = 2
    elif change == "revision":
        spec["revision"] = "0" * 40
    else:
        spec["tasks"][0]["archive"] = "../../unexpected.zip"
    with pytest.raises(ValueError):
        validate_protocol(holdout, pilot)


def test_holdout_run_records_its_own_configuration_and_result_namespace(tmp_path):
    config = tmp_path / "holdout.json"
    config.write_text('{"version":"separate-public-inputs"}')
    handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        run = BenchmarkRun(tmp_path, "editing_holdout", config_path=config)
        assert run.report["config_sha256"] == sha256(config)
        assert run.latest == tmp_path / "runs/editing_holdout/latest.json"
        assert not (tmp_path / "runs/editing/latest.json").exists()
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)


def test_holdout_verifies_its_official_sources_without_changing_pilot_receipts(tmp_path):
    media = tmp_path / "judge.py"
    media.write_text("# official verifier fixture")
    old = tmp_path / "upstream-receipts.json"
    old.write_text("{}")
    receipt = tmp_path / "holdout-receipts.json"
    receipt.write_text(json.dumps({"judge.py": sha256(media)}))
    verify_receipts(tmp_path, receipts_path=receipt)
    assert old.read_text() == "{}"
    media.write_text("# changed")
    with pytest.raises(ValueError, match="changed"):
        verify_receipts(tmp_path, receipts_path=receipt)
