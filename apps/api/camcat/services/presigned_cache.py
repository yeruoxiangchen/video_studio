from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from threading import Lock

SignatureKey = tuple[str, int, str | None]


class PresignedUrlCache:
    """Reuse real signatures briefly; never cache ownership or mutable session state."""

    def __init__(
        self, *, capacity: int = 2048, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if capacity < 1:
            raise ValueError("signature cache capacity must be positive")
        self._capacity = capacity
        self._clock = clock
        self._entries: OrderedDict[SignatureKey, tuple[float, str]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: SignatureKey, sign: Callable[[], str]) -> str:
        # Signing is local CPU work. Holding this lock also prevents concurrent
        # polling requests from regenerating the same missing signature.
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None and entry[0] > now:
                self._entries.move_to_end(key)
                return entry[1]
            url = sign()
            lifetime = min(30.0, key[1] / 10)
            if lifetime > 0:
                self._entries[key] = (now + lifetime, url)
                self._entries.move_to_end(key)
                while len(self._entries) > self._capacity:
                    self._entries.popitem(last=False)
            return url

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
