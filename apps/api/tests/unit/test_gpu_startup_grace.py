"""Pure resource-preflight tests; never allocate or terminate a GPU process."""

import pytest


def test_startup_waits_for_delayed_gpu_release_before_loading_models():
    from services.local_models.resources import gpu_plan_with_grace

    calls = []

    def devices():
        calls.append(1)
        return [
            {"index": i, "free_mib": 10000 if len(calls) == 1 else 24000, "total_mib": 24576}
            for i in (2, 4, 5)
        ]

    plan = gpu_plan_with_grace(
        devices,
        preferred_indices=[4, 5, 2],
        visual_precision="bfloat16",
        wait_seconds=1,
        interval_seconds=0.001,
    )
    assert len(calls) == 2
    assert [row["index"] for row in plan] == [4, 5, 2]


def test_busy_gpus_stop_preflight_after_the_budget_without_an_implicit_fallback():
    from services.local_models.resources import gpu_plan_with_grace

    with pytest.raises(RuntimeError, match="requested"):
        gpu_plan_with_grace(
            lambda: [],
            preferred_indices=[4, 5],
            visual_precision="nf4",
            wait_seconds=0,
            interval_seconds=0,
        )
