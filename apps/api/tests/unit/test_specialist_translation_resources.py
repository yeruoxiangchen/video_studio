import pytest


def test_specialist_gpu_plan_has_an_explicit_budget_and_allows_bounded_asr_sharing():
    from scripts.serve_specialist_translation import execution_plan

    plan = execution_plan(gpu_index=1, free_mib=20_000, total_mib=24_576)
    assert plan["gpu_index"] == 1
    assert plan["gpu_budget_mib"] == 4096


@pytest.mark.parametrize(
    "index,free,total", [(-1, 24000, 24576), (1, 4096, 24576), (0, 6000, 24576)]
)
def test_specialist_plan_rejects_invalid_gpu_or_excess_combined_occupancy(index, free, total):
    from scripts.serve_specialist_translation import execution_plan

    with pytest.raises(ValueError):
        execution_plan(gpu_index=index, free_mib=free, total_mib=total)


def test_specialist_runtime_must_match_the_unmodified_official_build():
    from scripts.serve_specialist_translation import validate_runtime

    validate_runtime({"revision": "fixed", "source_modified": False}, "fixed")
    for receipt in (
        {"revision": "other", "source_modified": False},
        {"revision": "fixed", "source_modified": True},
    ):
        with pytest.raises(ValueError):
            validate_runtime(receipt, "fixed")
