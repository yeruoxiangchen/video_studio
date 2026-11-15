"""Explicit CPU/CUDA profiles for the separately bounded ASR child process."""

from typing import Any


def asr_execution_plan(
    device: str, gpu_index: int | None, free_mib: int | None, reserved: set[int]
) -> dict[str, Any]:
    if device == "cpu":
        return {"device": "cpu", "compute_type": "int8", "cpu_threads": 4}
    if (
        device != "cuda"
        or gpu_index is None
        or gpu_index < 0
        or gpu_index in reserved
        or free_mib is None
        or free_mib < 5120
    ):
        raise ValueError("ASR CUDA requires a separate selected GPU with at least 5 GiB free")
    return {
        "device": "cuda",
        "compute_type": "float16",
        "cpu_threads": 2,
        "gpu_index": gpu_index,
        "memory_budget_mib": 4096,
    }
