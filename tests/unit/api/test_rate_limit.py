"""Distributed sliding-window rate limiter tests."""

from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from squid.api.rate_limit import (
    DistributedRateLimiter,
    LocalSlidingWindowRateLimiter,
    RateLimitDecision,
    RateLimitPolicy,
    RateLimitRequest,
    RateLimitState,
)
from squid.config import RateLimitConfig
from squid.core.errors import ErrorCode
from tests.unit.api.fakes import TEST_CONFIG, TEST_SYNERGY_SECRET, TEST_UUID, build_app


def request(policy: RateLimitPolicy, identity: str = "user:one") -> RateLimitRequest:
    return RateLimitRequest(policy, identity)


def allowed(policy: RateLimitPolicy) -> RateLimitDecision:
    return RateLimitDecision(
        allowed=True,
        states=(RateLimitState(policy, remaining=policy.limit - 1, reset_after=policy.window_seconds),),
    )


class StubLimiter:
    def __init__(self, *results: RateLimitDecision | Exception) -> None:
        self.results = list(results)
        self.calls = 0

    async def check(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision:
        del requests
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_local_limiter_is_bounded_per_identity_and_recovers_after_window() -> None:
    now = 10.0
    policy = RateLimitPolicy("test", 2, 5)
    limiter = LocalSlidingWindowRateLimiter(clock=lambda: now)

    first = await limiter.check((request(policy),))
    second = await limiter.check((request(policy),))
    other = await limiter.check((request(policy, "user:two"),))
    denied = await limiter.check((request(policy),))

    assert (first.allowed, first.states[0].remaining) == (True, 1)
    assert (second.allowed, second.states[0].remaining) == (True, 0)
    assert other.allowed is True
    assert denied.allowed is False
    assert denied.retry_after == 5

    now = 16.0
    recovered = await limiter.check((request(policy),))
    assert recovered.allowed is True


@pytest.mark.asyncio
async def test_local_limiter_does_not_partially_consume_a_rejected_policy_set() -> None:
    strict = RateLimitPolicy("strict", 1, 10)
    broad = RateLimitPolicy("broad", 2, 10)
    limiter = LocalSlidingWindowRateLimiter()
    await limiter.check((request(strict),))

    denied = await limiter.check((request(strict), request(broad)))
    broad_only = await limiter.check((request(broad),))

    assert denied.allowed is False
    assert [state.blocked for state in denied.states] == [True, False]
    assert broad_only.allowed is True
    assert broad_only.states[0].remaining == 1


@pytest.mark.asyncio
async def test_distributed_limiter_circuit_breaks_to_local_and_recovers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = 0.0
    policy = RateLimitPolicy("test", 3, 5)
    redis = StubLimiter(RedisConnectionError("offline"), allowed(policy))
    local = LocalSlidingWindowRateLimiter(clock=lambda: now)
    limiter = DistributedRateLimiter(local, redis, retry_seconds=5, clock=lambda: now)

    with caplog.at_level("INFO", logger="squid.api.rate_limit"):
        first = await limiter.check((request(policy),))
        second = await limiter.check((request(policy),))
        now = 6.0
        recovered = await limiter.check((request(policy),))

    assert first.allowed is True
    assert second.allowed is True
    assert recovered.allowed is True
    assert redis.calls == 2
    assert "process-local fallback" in caplog.text
    assert "distributed enforcement resumed" in caplog.text


@pytest.mark.asyncio
async def test_distributed_limiter_keeps_local_shadow_of_redis_successes() -> None:
    policy = RateLimitPolicy("test", 1, 30)
    redis = StubLimiter(allowed(policy), RedisConnectionError("offline"))
    limiter = DistributedRateLimiter(LocalSlidingWindowRateLimiter(), redis)

    first = await limiter.check((request(policy),))
    fallback = await limiter.check((request(policy),))

    assert first.allowed is True
    assert fallback.allowed is False
    assert fallback.retry_after == 30


def test_api_ip_limit_returns_quota_headers_and_retry_after() -> None:
    config = TEST_CONFIG.model_copy(
        update={
            "rate_limit": RateLimitConfig(
                ip_requests=1,
                principal_requests=10,
                write_requests=10,
                vote_requests=10,
            )
        }
    )
    app, database = build_app(config=config)

    with TestClient(app) as client:
        accepted = client.get("/v1/tags")
        denied = client.get("/v1/tags")

    assert accepted.status_code == 200
    assert accepted.headers["RateLimit-Policy"] == '"ip";q=1;w=300'
    assert accepted.headers["RateLimit"].startswith('"ip";r=0;t=')
    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "300"
    assert denied.json()["code"] == ErrorCode.RATE_LIMITED
    assert denied.json()["context"] == {"retry_after": 300}
    assert database.closed is True


def test_authenticated_write_reports_every_active_quota() -> None:
    app, _database = build_app()

    with TestClient(app) as client:
        response = client.post(
            "/verify",
            json={"uuid": str(TEST_UUID)},
            headers={"Authorization": TEST_SYNERGY_SECRET},
        )

    assert response.status_code == 201
    assert response.headers["RateLimit-Policy"] == ('"ip";q=600;w=300, "principal";q=300;w=300, "write";q=60;w=300')
    assert response.headers["RateLimit"].startswith('"ip";r=599;t=')


def test_health_and_preflight_requests_bypass_rate_limiting() -> None:
    config = TEST_CONFIG.model_copy(update={"rate_limit": RateLimitConfig(ip_requests=1)})
    app, _database = build_app(config=config)

    with TestClient(app) as client:
        first = client.get("/livez")
        second = client.get("/livez")
        preflight = client.options("/v1/tags")

    assert first.status_code == second.status_code == 200
    assert "RateLimit" not in first.headers
    assert preflight.status_code != 429
    assert "RateLimit" not in preflight.headers
