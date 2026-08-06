"""Sliding-window rate limiter tests."""

import pytest

from squid.api.rate_limit import SlidingWindowRateLimiter
from squid.core.errors import RateLimitedError


@pytest.mark.asyncio
async def test_limiter_is_bounded_per_principal_and_recovers_after_window() -> None:
    now = 10.0
    limiter = SlidingWindowRateLimiter(2, 5, clock=lambda: now)

    await limiter.check("one")
    await limiter.check("one")
    await limiter.check("two")
    with pytest.raises(RateLimitedError) as error:
        await limiter.check("one")
    assert error.value.retry_after == 5

    now = 16.0
    await limiter.check("one")
