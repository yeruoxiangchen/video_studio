import threading
import time

import pytest

from services.local_models.generation import GenerationLease, GenerationTimeout


def test_queued_generation_times_out_without_releasing_the_current_owner() -> None:
    lock = threading.Lock()
    lock.acquire()
    with pytest.raises(GenerationTimeout), GenerationLease(lock, 0.02):
        pytest.fail("A second generation acquired an occupied device")
    assert lock.locked()
    lock.release()


def test_failed_generation_releases_the_device_for_the_next_request() -> None:
    lock = threading.Lock()
    with pytest.raises(ValueError), GenerationLease(lock, 1):
        raise ValueError("decoder failed")
    with GenerationLease(lock, 1) as lease:
        assert lease.remaining() > 0


def test_late_completion_cannot_be_reported_as_success() -> None:
    lock = threading.Lock()
    with pytest.raises(GenerationTimeout), GenerationLease(lock, 0.01):
        time.sleep(0.03)
    assert not lock.locked()
