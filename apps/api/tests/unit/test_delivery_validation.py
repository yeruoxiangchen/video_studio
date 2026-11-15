"""Pure result-gate tests; no external model or benchmark execution is simulated."""

from copy import deepcopy

import pytest

from scripts.validate_edit_regressions import public_delivery_passed


def successful_report():
    return {
        "state": "completed",
        "summary": {"tasks": 2, "executed": 2, "execution_errors": 0, "full_reward": 2},
        "cases": [
            {
                "id": name,
                "state": "executed",
                "reward": 1.0,
                "verifier_calibration": {"oracle": 1.0, "missing_output": 0.0},
            }
            for name in ("assembly", "sequencing")
        ],
    }


def test_public_delivery_gate_accepts_two_executed_and_fully_scored_tasks():
    assert public_delivery_passed(successful_report(), {"assembly", "sequencing"})


def test_completed_evaluation_with_zero_delivered_tasks_does_not_start_followups():
    report = successful_report()
    report["summary"].update(executed=0, execution_errors=2, full_reward=0)
    for row in report["cases"]:
        row.update(state="error", reward=0.0)
    assert not public_delivery_passed(report, {"assembly", "sequencing"})


@pytest.mark.parametrize(
    "failure", ["partial_score", "harness_error", "bad_calibration", "missing_task"]
)
def test_public_followups_stop_for_incomplete_or_invalid_results(failure):
    report = deepcopy(successful_report())
    if failure == "partial_score":
        report["cases"][0]["reward"] = 0.5
    elif failure == "harness_error":
        report["cases"][0]["harness_error"] = "official verifier failed"
    elif failure == "bad_calibration":
        report["cases"][0]["verifier_calibration"]["oracle"] = 0.0
    else:
        report["cases"].pop()
    assert not public_delivery_passed(report, {"assembly", "sequencing"})
