"""Redis-backed sliding-window integration tests."""

import asyncio
from collections.abc import AsyncGenerator, Generator

import pytest
from redis.asyncio import Redis
from testcontainers.redis import RedisContainer

from squid.api.rate_limit import RateLimitPolicy, RateLimitRequest, RedisSlidingWindowRateLimiter


@pytest.fixture(scope="module")
def redis_container() -> Generator[RedisContainer]:
    with RedisContainer("redis:8.8.0-alpine") as container:
        yield container


@pytest.fixture
async def redis_clients(redis_container: RedisContainer) -> AsyncGenerator[tuple[Redis, Redis]]:
    host = redis_container.get_container_host_ip()
    port = int(redis_container.get_exposed_port(redis_container.port))
    first = Redis(host=host, port=port)
    second = Redis(host=host, port=port)
    await first.flushdb()
    try:
        yield first, second
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.asyncio
async def test_concurrent_replicas_cannot_overshoot_one_quota(redis_clients: tuple[Redis, Redis]) -> None:
    first_client, second_client = redis_clients
    limiters = (RedisSlidingWindowRateLimiter(first_client), RedisSlidingWindowRateLimiter(second_client))
    policy = RateLimitPolicy("integration", 10, 30)
    request = RateLimitRequest(policy, "user:shared")

    decisions = await asyncio.gather(*(limiters[index % 2].check((request,)) for index in range(30)))

    assert sum(decision.allowed for decision in decisions) == 10
    assert sum(not decision.allowed for decision in decisions) == 20
    assert all(decision.states[0].remaining == 0 for decision in decisions if not decision.allowed)


@pytest.mark.asyncio
async def test_registered_script_recovers_after_cache_flush_and_window_expiry(
    redis_clients: tuple[Redis, Redis],
) -> None:
    client, _other = redis_clients
    limiter = RedisSlidingWindowRateLimiter(client)
    policy = RateLimitPolicy("expiry", 1, 1)
    request = RateLimitRequest(policy, "user:expiry")

    assert (await limiter.check((request,))).allowed is True
    assert (await limiter.check((request,))).allowed is False
    await client.script_flush()
    await asyncio.sleep(1.05)

    recovered = await limiter.check((request,))
    assert recovered.allowed is True
