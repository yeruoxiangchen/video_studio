"""Bound startup resource waiting with Tenacity; model loading starts after a successful plan."""

from collections.abc import Callable
from typing import Any, Literal

from tenacity import Retrying, retry_if_exception_type, stop_after_delay, wait_fixed

from .protocol import local_gpu_plan


def gpu_plan_with_grace(
    read_devices: Callable[[], list[dict[str, Any]]],
    *,
    preferred_indices: list[int] | None,
    visual_precision: Literal["nf4", "bfloat16"],
    wait_seconds: float = 30,
    interval_seconds: float = 2,
) -> list[dict[str, Any]]:
    for attempt in Retrying(
        retry=retry_if_exception_type(RuntimeError),
        stop=stop_after_delay(wait_seconds),
        wait=wait_fixed(interval_seconds),
        reraise=True,
    ):
        with attempt:
            return local_gpu_plan(
                read_devices(),
                preferred_indices=preferred_indices,
                visual_precision=visual_precision,
            )
    raise AssertionError("GPU resource preflight did not return a plan")
