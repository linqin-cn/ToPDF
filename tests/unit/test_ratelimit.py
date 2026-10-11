"""令牌桶限流器测试。"""

from __future__ import annotations

from resume_pdf.core.ratelimit import IDLE_BUCKET_TTL_SECONDS, TokenBucketLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_burst_then_throttled() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(rpm=60, burst=3, clock=clock)

    assert [limiter.check("k") for _ in range(3)] == [None, None, None]

    retry_after = limiter.check("k")
    assert retry_after is not None and retry_after >= 1


def test_refill_respects_capacity() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(rpm=60, burst=2, clock=clock)

    limiter.check("k")
    limiter.check("k")
    assert limiter.check("k") is not None

    # 60 rpm = 每秒 1 个令牌；等待 1 秒只补 1 个，不会超过 burst。
    clock.advance(1.0)
    assert limiter.check("k") is None
    assert limiter.check("k") is not None

    clock.advance(600.0)
    assert limiter.check("k") is None
    assert limiter.check("k") is None
    assert limiter.check("k") is not None


def test_identities_are_isolated() -> None:
    limiter = TokenBucketLimiter(rpm=60, burst=1, clock=FakeClock())

    assert limiter.check("key:a") is None
    assert limiter.check("key:a") is not None
    assert limiter.check("key:b") is None
    assert limiter.check("ip:1.2.3.4") is None


def test_retry_after_scales_with_rate() -> None:
    clock = FakeClock()
    slow = TokenBucketLimiter(rpm=1, burst=1, clock=clock)

    assert slow.check("k") is None
    # 1 rpm ≈ 每 60 秒补一个令牌，因此建议退避接近 60 秒。
    assert slow.check("k") == 60


def test_idle_buckets_are_pruned_and_reset_clears() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(rpm=60, burst=1, clock=clock)

    assert limiter.check("k") is None
    clock.advance(IDLE_BUCKET_TTL_SECONDS + 1)
    # 桶被清理后重新按满容量计算。
    assert limiter.check("k") is None

    limiter.reset()
    assert limiter.check("k") is None


def test_rate_per_second_matches_rpm() -> None:
    assert TokenBucketLimiter(rpm=120, burst=10).rate_per_second == 2.0
