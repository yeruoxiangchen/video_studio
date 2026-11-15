"""The ASR GPU must be explicitly selected, idle enough and separate from core roles."""

import pytest


def test_cpu_profile_does_not_require_a_gpu():
    from services.local_models.asr_resources import asr_execution_plan

    assert asr_execution_plan("cpu", None, None, set()) == {
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 4,
    }


def test_cuda_profile_has_an_explicit_budget_and_rejects_busy_or_reserved_devices():
    from services.local_models.asr_resources import asr_execution_plan

    plan = asr_execution_plan("cuda", 1, 24000, {2, 3, 4})
    assert plan["gpu_index"] == 1 and plan["compute_type"] == "float16"
    assert plan["memory_budget_mib"] == 4096
    for index, free, reserved in [(1, 2000, set()), (2, 24000, {2, 3}), (None, 24000, set())]:
        with pytest.raises(ValueError):
            asr_execution_plan("cuda", index, free, reserved)
