# ADR 0076: Keep the atomic multi-policy rate-limit primitive application-owned

Status: accepted (2026-08-31)

## Context

One Squid request can consume several quotas at once: the pre-authentication IP ceiling and, after authentication,
caller, write, vote, suggestion, render, or Minecraft-flow policies. Rejection must not consume any of them. A Redis
failure must immediately use a bounded local limiter that has shadowed successful Redis decisions, and recovery must
not admit a request that the shadow already rejects. The HTTP boundary then publishes one combined `RateLimit` and
`RateLimit-Policy` decision in stable policy order.

The candidate spike compared the current implementation with the current released surfaces of three libraries:

- [`limits` 5.8.0](https://limits.readthedocs.io/en/stable/api.html), including its async Redis moving-window
  strategy;
- [`SlowAPI` 0.1.10](https://slowapi.readthedocs.io/en/latest/api/), a FastAPI/Starlette adapter over `limits`;
- [`fastapi-limiter` 0.2.0](https://github.com/long2ice/fastapi-limiter/tree/v0.2.0), a FastAPI dependency and
  middleware adapter over `pyrate-limiter`.

The spike used the behavior already pinned by `tests/unit/api/test_rate_limit.py` and
`tests/integration/api/test_rate_limit.py`; it did not compare decorator ergonomics or raw single-counter throughput,
because neither decides whether a primitive can preserve Squid's contract.

| Required behavior | `limits` 5.8.0 | SlowAPI 0.1.10 | `fastapi-limiter` 0.2.0 |
| --- | --- | --- | --- |
| Exact distributed rolling window | Yes, one rate-limit item at a time | Exposes the `limits` strategies | Exposes its backend limiter |
| Atomically accept or reject several differently keyed policies | No batch decision API | Multiple limits are evaluated by the adapter, not one storage transaction | Multiple dependencies/limiters are evaluated separately |
| No partial consumption when a later policy rejects | No | No | No |
| Keep a local shadow current while Redis succeeds | No | Its documented in-memory fallback is selected when storage fails; it is not a success-path shadow | No adapter contract for it |
| Recover to Redis without double-spending the shadow | No | No | No |
| Return every policy's remaining/reset/blocked state for one combined header | No batch result | Adapter-owned legacy headers | Adapter callback per limiter |
| Preserve Squid's pre-auth IP and post-auth caller classification | Primitive only | Would still require Squid transport policy | Would still require Squid transport policy |

`limits` is the closest primitive candidate: it has maintained async Redis storage and moving-window support. Its
public operation, however, consumes one rate-limit item. Calling it once per applicable policy creates the exact
partial-consumption failure the current single Lua script prevents. Wrapping those calls in another Lua script would
retain almost all of Squid's current Redis primitive while adding a second storage abstraction.

SlowAPI documents both multiple endpoint limits and an in-memory fallback, but those solve different problems. The
limits are not one all-or-nothing storage decision. A fallback created only after Redis fails has not observed the
successful Redis requests that preceded failure, so it cannot provide the conservative continuity the local shadow
provides. Its decorator and response-header ownership would also duplicate Squid's authentication boundary and
combined-header renderer.

Fastapi-limiter offers dependency and middleware integration, including multiple dependencies on one route. That is
again sequential enforcement rather than one atomic multi-key decision, and its transport integration does not
supply the shadow/recovery contract or Squid's combined state record.

## Decision

Keep `RateLimiter`, `RedisSlidingWindowRateLimiter`, `LocalSlidingWindowRateLimiter`, and
`DistributedRateLimiter` application-owned. Keep the Redis operation as one script over every applicable policy, and
keep policy classification and header rendering above that primitive.

Do not add one of the candidate packages merely to replace the script. Reconsider a maintained primitive when it can
accept a sequence of independently keyed policies in one atomic call and return per-policy state, or when Redis gains
a standard command with that contract. Any future replacement runs the existing unit and two-client Redis integration
matrix unchanged before the dependency is accepted.

## Consequences

Squid owns a small amount of storage-algorithm code and must track Redis compatibility itself. The script stays narrow:
server time, pruning, one all-or-nothing admission decision, insertion, expiry, and per-policy state. Route vocabulary,
fallback transitions, observability, and structured HTTP headers remain normal typed Python code.

The local limiter is not presented as cross-process protection. It is bounded, process-local continuity for a Redis
incident, deliberately shadowed during healthy operation. Separate API processes can each admit their local allowance
while Redis is unavailable; operational counters expose that degraded mode.
