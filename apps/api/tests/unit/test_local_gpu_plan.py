import pytest

from services.local_models.protocol import local_gpu_plan


def test_bfloat16_visual_profile_uses_an_independent_idle_gpu_and_explicit_budget():
    devices = [
        {"index": index, "uuid": str(index), "free_mib": 23500, "total_mib": 24576}
        for index in (2, 4, 5)
    ]
    plan = local_gpu_plan(devices, preferred_indices=[4, 5, 2], visual_precision="bfloat16")
    assert [(row["role"], row["index"]) for row in plan] == [
        ("embedding", 4),
        ("reranker", 5),
        ("visual", 2),
    ]
    assert plan[2]["precision"] == "bfloat16"
    assert plan[2]["cuda_device"] == 2
    assert plan[2]["budget_shared"] is False
    assert plan[2]["budget_mib"] == 21135


def test_bfloat16_profile_does_not_silently_use_a_busy_or_missing_third_gpu():
    devices = [
        {"index": index, "uuid": str(index), "free_mib": 23500, "total_mib": 24576}
        for index in (4, 5)
    ]
    with pytest.raises(RuntimeError, match="3"):
        local_gpu_plan(devices, visual_precision="bfloat16")


def test_profile_gpu_count_mismatch_is_a_configuration_error():
    with pytest.raises(ValueError, match="requires 3"):
        local_gpu_plan([], preferred_indices=[4, 5], visual_precision="bfloat16")


def test_two_idle_gpus_keep_canonical_embedding_in_bfloat16_and_share_generation_card() -> None:
    devices = [
        {"index": 0, "uuid": "training", "free_mib": 14000, "total_mib": 24576},
        {"index": 3, "uuid": "idle-a", "free_mib": 24126, "total_mib": 24576},
        {"index": 5, "uuid": "idle-b", "free_mib": 24126, "total_mib": 24576},
    ]
    plan = {item["role"]: item for item in local_gpu_plan(devices)}
    assert {item["index"] for item in plan.values()} == {3, 5}
    assert plan["embedding"]["precision"] == "bfloat16"
    assert plan["reranker"]["index"] == plan["visual"]["index"]
    assert plan["reranker"]["precision"] == plan["visual"]["precision"] == "nf4"
    assert plan["visual"]["budget_shared"]
    assert all(item["memory_fraction"] == 0.86 for item in plan.values())


def test_one_idle_gpu_fails_before_loading_any_model() -> None:
    with pytest.raises(RuntimeError, match="Two idle GPUs"):
        local_gpu_plan([{"index": 3, "uuid": "idle-a", "free_mib": 24126, "total_mib": 24576}])


def test_explicit_gpu_indices_preserve_model_affinity_and_do_not_fall_back():
    devices = [{"index": i, "free_mib": 24000, "total_mib": 24576} for i in (2, 4, 5)]
    assert [r["index"] for r in local_gpu_plan(devices, preferred_indices=[4, 5])] == [4, 5, 5]
    with pytest.raises(RuntimeError, match="requested"):
        local_gpu_plan(devices[:-1], preferred_indices=[4, 5])
