"""Distributed sliding-window abuse controls for the HTTP API."""

import asyncio
import hashlib
import logging
import math
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Annotated, Protocol, cast, override
from uuid import uuid4

from fastapi import Depends, Request
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError, ResponseError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from squid.api.errors import handle_squid_error
from squid.api.security import Caller, current_caller
from squid.config import RateLimitConfig
from squid.core.errors import RateLimitedError
from squid.observability import add_counter

logger = logging.getLogger(__name__)

_KEY_PREFIX = "{squid-rate-limit}:v1"
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_VOTE_WRITE_PATH = "/v1/vote-sessions/{vote_session_id}/votes"
_SUGGEST_PATH = "/v1/suggest/{source}"
_MINECRAFT_CHALLENGE_START_PATHS = frozenset(
    {
        "/v1/minecraft/auth/paper/challenges",
        "/v1/minecraft/auth/fabric/challenges",
    }
)
_MINECRAFT_CHALLENGE_EXCHANGE_PATHS = frozenset(
    {
        "/v1/minecraft/auth/paper/challenges/exchange",
        "/v1/minecraft/auth/fabric/challenges/exchange",
    }
)
_MINECRAFT_CHALLENGE_APPROVAL_PATH = "/v1/minecraft/auth/challenges/approval"
_BYPASS_PATHS = frozenset({"/livez", "/health", "/readyz"})

_SLIDING_WINDOW_SCRIPT = """
local time = redis.call('TIME')
local now_ms = (tonumber(time[1]) * 1000) + math.floor(tonumber(time[2]) / 1000)
local request_id = ARGV[(#KEYS * 2) + 1]
local counts = {}
local oldest = {}
local blocked = {}
local allowed = 1

for index = 1, #KEYS do
    local limit = tonumber(ARGV[((index - 1) * 2) + 1])
    local window_ms = tonumber(ARGV[((index - 1) * 2) + 2])
    redis.call('ZREMRANGEBYSCORE', KEYS[index], '-inf', now_ms - window_ms)
    counts[index] = redis.call('ZCARD', KEYS[index])
    blocked[index] = counts[index] >= limit
    if blocked[index] then
        allowed = 0
    end
    local first = redis.call('ZRANGE', KEYS[index], 0, 0, 'WITHSCORES')
    if #first > 0 then
        oldest[index] = tonumber(first[2])
    end
end

if allowed == 1 then
    for index = 1, #KEYS do
        local window_ms = tonumber(ARGV[((index - 1) * 2) + 2])
        redis.call('ZADD', KEYS[index], now_ms, request_id .. ':' .. index)
        redis.call('PEXPIRE', KEYS[index], window_ms)
        counts[index] = counts[index] + 1
        if oldest[index] == nil then
            oldest[index] = now_ms
        end
    end
end

local result = {allowed}
for index = 1, #KEYS do
    local limit = tonumber(ARGV[((index - 1) * 2) + 1])
    local window_ms = tonumber(ARGV[((index - 1) * 2) + 2])
    local remaining = math.max(0, limit - counts[index])
    local reset_ms = window_ms
    if oldest[index] ~= nil then
        reset_ms = math.max(1, oldest[index] + window_ms - now_ms)
    end
    table.insert(result, remaining)
    table.insert(result, math.max(1, math.ceil(reset_ms / 1000)))
    table.insert(result, allowed == 0 and blocked[index] and 1 or 0)
end
return result
"""


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """One exact request quota over a rolling window."""

    name: str
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitRequest:
    """Apply one policy to a stable caller identity."""

    policy: RateLimitPolicy
    identity: str


@dataclass(frozen=True, slots=True)
class RateLimitState:
    """Quota state after an accepted request or at a rejection boundary."""

    policy: RateLimitPolicy
    remaining: int
    reset_after: int
    blocked: bool = False


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Atomic decision across every policy applicable to a request."""

    allowed: bool
    states: tuple[RateLimitState, ...]

    @property
    def retry_after(self) -> int:
        """Return when every policy that blocked the request has capacity."""
        return max((state.reset_after for state in self.states if state.blocked), default=1)


@dataclass(frozen=True, slots=True)
class ApiRateLimitPolicies:
    """Named policies selected by the API transport."""

    ip: RateLimitPolicy
    caller: RateLimitPolicy
    write: RateLimitPolicy
    vote: RateLimitPolicy
    suggest: RateLimitPolicy
    minecraft_challenge_start: RateLimitPolicy
    minecraft_challenge_exchange: RateLimitPolicy
    minecraft_challenge_approval: RateLimitPolicy

    @classmethod
    def from_config(cls, config: RateLimitConfig) -> ApiRateLimitPolicies:
        window = config.window_seconds
        return cls(
            ip=RateLimitPolicy("ip", config.ip_requests, window),
            caller=RateLimitPolicy("principal", config.principal_requests, window),
            write=RateLimitPolicy("write", config.write_requests, window),
            vote=RateLimitPolicy("vote", config.vote_requests, window),
            suggest=RateLimitPolicy("suggest", config.suggest_requests, window),
            minecraft_challenge_start=RateLimitPolicy(
                "minecraft-challenge-start",
                config.minecraft_challenge_start_requests,
                window,
            ),
            minecraft_challenge_exchange=RateLimitPolicy(
                "minecraft-challenge-exchange",
                config.minecraft_challenge_exchange_requests,
                window,
            ),
            minecraft_challenge_approval=RateLimitPolicy(
                "minecraft-challenge-approval",
                config.minecraft_challenge_approval_requests,
                window,
            ),
        )


class RateLimiter(Protocol):
    """Atomic multi-policy limiter boundary."""

    async def check(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision: ...


class LocalSlidingWindowRateLimiter:
    """Bounded process-local limiter used as a Redis shadow and fallback."""

    def __init__(
        self,
        *,
        max_keys: int = 2_048,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._max_keys = max_keys
        self._clock = clock
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def check(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision:
        """Check and record all policies without partially consuming a rejected request."""
        if not requests:
            return RateLimitDecision(allowed=True, states=())
        now = self._clock()
        async with self._lock:
            prepared: list[tuple[RateLimitRequest, str, deque[float]]] = []
            for request in requests:
                storage_key = _storage_key(request)
                events = self._events.get(storage_key, deque())
                cutoff = now - request.policy.window_seconds
                while events and events[0] <= cutoff:
                    events.popleft()
                if storage_key in self._events:
                    if events:
                        self._events.move_to_end(storage_key)
                    else:
                        del self._events[storage_key]
                prepared.append((request, storage_key, events))

            allowed = all(len(events) < request.policy.limit for request, _, events in prepared)
            if allowed:
                for _request, storage_key, events in prepared:
                    if storage_key not in self._events:
                        while len(self._events) >= self._max_keys:
                            self._events.popitem(last=False)
                        self._events[storage_key] = events
                    events.append(now)
                    self._events.move_to_end(storage_key)

            states = tuple(
                RateLimitState(
                    policy=request.policy,
                    remaining=max(0, request.policy.limit - len(events)),
                    reset_after=_reset_after(events, now, request.policy.window_seconds),
                    blocked=not allowed and len(events) >= request.policy.limit,
                )
                for request, _, events in prepared
            )
            return RateLimitDecision(allowed, states)


class RedisSlidingWindowRateLimiter:
    """Exact cross-process limiter implemented by one atomic Redis script."""

    def __init__(self, client: Redis) -> None:
        self._client = client
        self._script = client.register_script(_SLIDING_WINDOW_SCRIPT)

    async def check(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision:
        if not requests:
            return RateLimitDecision(allowed=True, states=())
        keys = [_storage_key(request) for request in requests]
        args: list[int | str] = []
        for request in requests:
            args.extend((request.policy.limit, request.policy.window_seconds * 1000))
        args.append(uuid4().hex)
        raw_result = await self._script(keys=keys, args=args)
        if not isinstance(raw_result, list) or len(raw_result) != 1 + len(requests) * 3:
            msg = "Redis returned a malformed rate-limit decision."
            raise ResponseError(msg)
        values = [_redis_integer(value) for value in cast(list[object], raw_result)]
        allowed = values[0] == 1
        states = tuple(
            RateLimitState(
                policy=request.policy,
                remaining=values[1 + index * 3],
                reset_after=values[2 + index * 3],
                blocked=values[3 + index * 3] == 1,
            )
            for index, request in enumerate(requests)
        )
        return RateLimitDecision(allowed, states)


class DistributedRateLimiter:
    """Prefer Redis while maintaining a conservative process-local shadow."""

    def __init__(
        self,
        local: LocalSlidingWindowRateLimiter,
        redis: RateLimiter | None = None,
        *,
        redis_client: Redis | None = None,
        retry_seconds: float = 5.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._local = local
        self._redis = redis
        self._redis_client = redis_client
        self._retry_seconds = retry_seconds
        self._clock = clock
        self._degraded = False
        self._retry_at = 0.0
        self._probe_lock = asyncio.Lock()

    async def check(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision:
        """Return a Redis decision, or the local shadow decision while degraded."""
        if self._redis is None:
            return self._record(await self._local.check(requests), backend="local")

        if self._degraded:
            if self._clock() < self._retry_at or self._probe_lock.locked():
                return self._record(await self._local.check(requests), backend="local")
            async with self._probe_lock:
                if self._clock() < self._retry_at:
                    return self._record(await self._local.check(requests), backend="local")
                redis_decision = await self._try_redis(requests)
        else:
            redis_decision = await self._try_redis(requests)

        if redis_decision is None:
            return self._record(await self._local.check(requests), backend="local")
        if not redis_decision.allowed:
            return self._record(redis_decision, backend="redis")

        local_decision = await self._local.check(requests)
        if not local_decision.allowed:
            return self._record(local_decision, backend="local")
        return self._record(redis_decision, backend="redis")

    async def aclose(self) -> None:
        """Close the process-owned Redis connection pool."""
        if self._redis_client is not None:
            await self._redis_client.aclose()

    async def _try_redis(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision | None:
        assert self._redis is not None
        try:
            decision = await self._redis.check(requests)
        except RedisError, OSError:
            if not self._degraded:
                logger.warning("Redis rate limiting is unavailable; using the process-local fallback")
                add_counter("squid.api.rate_limit.backend_transitions", attributes={"squid.backend": "local"})
            self._degraded = True
            self._retry_at = self._clock() + self._retry_seconds
            return None
        if self._degraded:
            logger.info("Redis rate limiting recovered; distributed enforcement resumed")
            add_counter("squid.api.rate_limit.backend_transitions", attributes={"squid.backend": "redis"})
        self._degraded = False
        self._retry_at = 0.0
        return decision

    @staticmethod
    def _record(decision: RateLimitDecision, *, backend: str) -> RateLimitDecision:
        add_counter(
            "squid.api.rate_limit.decisions",
            attributes={
                "squid.backend": backend,
                "squid.outcome": "allowed" if decision.allowed else "denied",
                "squid.policies": ",".join(state.policy.name for state in decision.states),
            },
        )
        return decision


def create_rate_limiter(config: RateLimitConfig) -> tuple[DistributedRateLimiter, ApiRateLimitPolicies]:
    """Create one process-owned limiter and its immutable policy set."""
    local = LocalSlidingWindowRateLimiter(max_keys=config.local_max_keys)
    policies = ApiRateLimitPolicies.from_config(config)
    if config.redis_url is None:
        logger.info("No Redis rate-limit URL is configured; using process-local enforcement")
        return DistributedRateLimiter(local), policies

    client = Redis.from_url(
        config.redis_url.get_secret_value(),
        socket_connect_timeout=config.redis_timeout_seconds,
        socket_timeout=config.redis_timeout_seconds,
        retry=Retry(NoBackoff(), 0),
        retry_on_timeout=False,
        max_connections=20,
    )
    redis_limiter = RedisSlidingWindowRateLimiter(client)
    return (
        DistributedRateLimiter(
            local,
            redis_limiter,
            redis_client=client,
            retry_seconds=config.redis_retry_seconds,
        ),
        policies,
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce the pre-authentication IP ceiling and publish quota headers."""

    @override
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS" or request.url.path in _BYPASS_PATHS:
            return await call_next(request)
        if not hasattr(request.app.state, "rate_limiter"):
            # Schema loaders may inspect OpenAPI without entering the ASGI lifespan.
            return await call_next(request)

        limiter, policies = _limiter_state(request)
        decision = await limiter.check((RateLimitRequest(policies.ip, _client_identity(request)),))
        request.state.rate_limit_states = list(decision.states)
        if decision.allowed:
            response = await call_next(request)
        else:
            response = await handle_squid_error(request, RateLimitedError(decision.retry_after))
        _set_rate_limit_headers(response, _states(request))
        return response


async def enforce_route_rate_limits(
    request: Request,
    caller: Annotated[Caller, Depends(current_caller)],
) -> None:
    """Apply authenticated, mutation, and vote quotas to a matched API route."""
    limiter, policies = _limiter_state(request)
    checks: list[RateLimitRequest] = []
    identity = caller.subject if caller.kind != "anonymous" else _client_identity(request)
    if caller.kind != "anonymous":
        checks.append(RateLimitRequest(policies.caller, identity))
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    request_path = request.url.path
    if request_path in _MINECRAFT_CHALLENGE_START_PATHS:
        checks.append(RateLimitRequest(policies.minecraft_challenge_start, identity))
    elif request_path in _MINECRAFT_CHALLENGE_EXCHANGE_PATHS:
        checks.append(RateLimitRequest(policies.minecraft_challenge_exchange, identity))
    elif request_path == _MINECRAFT_CHALLENGE_APPROVAL_PATH:
        checks.append(RateLimitRequest(policies.minecraft_challenge_approval, identity))
    elif request.method in _WRITE_METHODS:
        checks.append(RateLimitRequest(policies.write, identity))
    if caller.kind != "anonymous" and route_path == _VOTE_WRITE_PATH:
        checks.append(RateLimitRequest(policies.vote, identity))
    if route_path == _SUGGEST_PATH:
        # Its own bucket in both directions: one user typing must not exhaust their read quota,
        # and a client that forgets to debounce must not exhaust everyone else's.
        checks.append(RateLimitRequest(policies.suggest, identity))
    if not checks:
        return

    decision = await limiter.check(checks)
    _states(request).extend(decision.states)
    if not decision.allowed:
        raise RateLimitedError(decision.retry_after)


def _limiter_state(request: Request) -> tuple[DistributedRateLimiter, ApiRateLimitPolicies]:
    return (
        cast(DistributedRateLimiter, request.app.state.rate_limiter),
        cast(ApiRateLimitPolicies, request.app.state.rate_limit_policies),
    )


def _states(request: Request) -> list[RateLimitState]:
    states = getattr(request.state, "rate_limit_states", None)
    if states is None:
        states = []
        request.state.rate_limit_states = states
    return cast(list[RateLimitState], states)


def _client_identity(request: Request) -> str:
    host = request.client.host if request.client is not None else "unknown"
    return f"ip:{host.casefold()}"


def _storage_key(request: RateLimitRequest) -> str:
    identity_hash = hashlib.sha256(request.identity.encode()).hexdigest()
    return f"{_KEY_PREFIX}:{request.policy.name}:{identity_hash}"


def _reset_after(events: deque[float], now: float, window_seconds: int) -> int:
    if not events:
        return window_seconds
    return max(1, math.ceil(events[0] + window_seconds - now))


def _redis_integer(value: object) -> int:
    if not isinstance(value, int | bytes | str):
        msg = "Redis returned a non-integer rate-limit value."
        raise ResponseError(msg)
    return int(value)


def _set_rate_limit_headers(response: Response, states: Sequence[RateLimitState]) -> None:
    if not states:
        return
    response.headers["RateLimit-Policy"] = ", ".join(
        f'"{state.policy.name}";q={state.policy.limit};w={state.policy.window_seconds}' for state in states
    )
    response.headers["RateLimit"] = ", ".join(
        f'"{state.policy.name}";r={state.remaining};t={state.reset_after}' for state in states
    )
