"""Fixed-window request limiter backed by Redis."""

import logging
import time
from collections.abc import Callable
from typing import Protocol

logger = logging.getLogger(__name__)


class RateLimiter(Protocol):
    async def allow(self, key: str) -> bool: ...


class RedisRateLimiter:
    """Counts requests per key per minute.

    Redis outages fail open so destination search stays usable; the upstream key's own
    quota remains the hard cost ceiling.
    """

    def __init__(self, redis_factory: Callable, limit: int, window_seconds: int = 60, prefix: str = "ratelimit"):
        self._redis_factory = redis_factory
        self._limit = limit
        self._window = window_seconds
        self._prefix = prefix

    async def allow(self, key: str) -> bool:
        bucket = int(time.time()) // self._window
        redis_key = f"{self._prefix}:{key}:{bucket}"
        try:
            redis = self._redis_factory()
            async with redis.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, self._window * 2)
                count, _ = await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - any cache failure must not break search
            logger.warning("Rate limiter unavailable: %s", type(exc).__name__)
            return True
        return int(count) <= self._limit
