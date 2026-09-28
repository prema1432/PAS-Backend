"""In-memory sliding-window rate limiting.

Dependency-free and per-process: good enough for a single instance, and the
interface stays the same if this is later backed by Redis for multi-instance
deployments.

The two application limiters live here, built from settings at import time, so
any module can meter its own sensitive endpoint without importing `app.main`
(which would be circular). `app/main.py` imports and re-exports them for the
middleware; tests that swap a limiter for a controllable one patch the module
that uses it, exactly as before.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

from app.core.config import settings


class SlidingWindowRateLimiter:
    """Allow at most `limit` events per `window_seconds` for a given key."""

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = max(1, limit)
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Register a hit for `key`.

        Returns (allowed, retry_after_seconds). `retry_after` is 0 when the
        request is allowed.
        """
        now = self._clock()
        cutoff = now - self.window_seconds

        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - hits[0])) + 1)
                return False, retry_after

            hits.append(now)
            return True, 0

    def reset(self, key: str | None = None) -> None:
        """Forget history for one key (or everything)."""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)

    def tracked_keys(self) -> int:
        """Number of keys currently tracked (handy for tests/ops)."""
        with self._lock:
            return len(self._hits)


def build_limiters(
    per_minute: int, auth_per_minute: int
) -> tuple[SlidingWindowRateLimiter, SlidingWindowRateLimiter]:
    """Create (default, auth) limiters from per-minute budgets."""
    return (
        SlidingWindowRateLimiter(limit=per_minute, window_seconds=60.0),
        SlidingWindowRateLimiter(limit=auth_per_minute, window_seconds=60.0),
    )


#: The application's limiters: everything, and the tighter credential-stuffing
#: budget shared by password sign-in and the customer portal.
default_limiter, auth_limiter = build_limiters(
    per_minute=settings.rate_limit_per_minute,
    auth_per_minute=settings.auth_rate_limit_per_minute,
)
