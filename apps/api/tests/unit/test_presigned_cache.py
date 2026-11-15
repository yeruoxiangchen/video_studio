from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from camcat.services.presigned_cache import PresignedUrlCache


def test_repeated_concurrent_reads_sign_once_and_refresh_before_expiry() -> None:
    clock = [100.0]
    cache = PresignedUrlCache(clock=lambda: clock[0])
    calls: list[int] = []

    def sign() -> str:
        calls.append(1)
        return f"signed-{len(calls)}"

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: cache.get(("media", 3600, ""), sign), range(64)))
    assert set(results) == {"signed-1"}
    assert len(calls) == 1
    clock[0] += 30
    assert cache.get(("media", 3600, ""), sign) == "signed-2"


def test_short_lived_urls_expire_from_cache_at_tenth_of_requested_lifetime() -> None:
    clock = [100.0]
    cache = PresignedUrlCache(clock=lambda: clock[0])
    assert cache.get(("media", 10, ""), lambda: "first") == "first"
    clock[0] += 0.9
    assert cache.get(("media", 10, ""), lambda: "unexpected") == "first"
    clock[0] += 0.1
    assert cache.get(("media", 10, ""), lambda: "renewed") == "renewed"


def test_signature_parameters_are_isolated_and_capacity_is_bounded() -> None:
    cache = PresignedUrlCache(capacity=2)
    assert cache.get(("media", 3600, ""), lambda: "play") == "play"
    assert cache.get(("media", 3600, "one.mp4"), lambda: "one") == "one"
    assert cache.get(("media", 3600, "two.mp4"), lambda: "two") == "two"
    assert cache.get(("media", 60, ""), lambda: "short") == "short"
    assert cache.get(("media", 3600, ""), lambda: "regenerated") == "regenerated"
    assert len(cache) == 2
