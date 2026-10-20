"""Per-client rate limiting (token bucket)."""

import threading
import time
from collections.abc import Callable


class RateLimiter:
    """Allows ``per_minute`` calls per client, refilled continuously, with bursts up to that.

    In-process only: one server process, one budget. A multi-instance deploy would need a
    shared store (e.g. Redis); see the README's known limits.
    """

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be at least 1")
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0  # tokens per second
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # client -> (tokens, last time)
        self._lock = threading.Lock()

    def acquire(self, client_id: str) -> float | None:
        """Spend one call. Returns None if allowed, else seconds until the next call is."""
        with self._lock:
            now = self._clock()
            tokens, last = self._buckets.get(client_id, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._rate)
            if tokens >= 1:
                self._buckets[client_id] = (tokens - 1, now)
                return None
            self._buckets[client_id] = (tokens, now)
            return (1 - tokens) / self._rate
