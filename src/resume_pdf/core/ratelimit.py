"""令牌桶限流。

单进程、单事件循环下的内存实现：按调用方标识（API Key 或客户端 IP）计数，
超过配额时快速失败，不占用渲染并发闸门。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

SECONDS_PER_MINUTE = 60.0
IDLE_BUCKET_TTL_SECONDS = 600.0


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class TokenBucketLimiter:
    """每标识一个桶：容量为 burst，按 rpm/60 的速率补充。"""

    rpm: int
    burst: int
    clock: Callable[[], float] = time.monotonic
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    @property
    def rate_per_second(self) -> float:
        return self.rpm / SECONDS_PER_MINUTE

    def check(self, key: str) -> int | None:
        """返回 None 表示放行，否则返回建议的 Retry-After 秒数。"""
        now = self._now()
        self._prune(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.burst), updated_at=now)
            self._buckets[key] = bucket

        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(float(self.burst), bucket.tokens + elapsed * self.rate_per_second)
        bucket.updated_at = now

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return None

        deficit = 1.0 - bucket.tokens
        return max(1, math.ceil(deficit / self.rate_per_second))

    def reset(self) -> None:
        self._buckets.clear()

    def _now(self) -> float:
        return float(self.clock())

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.updated_at > IDLE_BUCKET_TTL_SECONDS
        ]
        for key in stale:
            del self._buckets[key]
