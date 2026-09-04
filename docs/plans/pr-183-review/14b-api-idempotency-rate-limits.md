# PR #183 Review 14B: API Idempotency and Rate Limits

## Scope

This plan covers twelve review threads in `squid/api/idempotency.py`, `squid/api/rate_limit.py`, the durable
idempotency model, the advisory-lock namespace, and the rate-limit test double. It owns HTTP enforcement and durable
replay contracts, not endpoint-specific draft idempotency (14D) or Minecraft credential partitioning (14I).

## Current-state findings

### Idempotency is intentionally split at the authentication boundary

Current idempotency has two parts: a FastAPI dependency reserves a key after authentication has established a
server-derived caller, and pure ASGI middleware buffers/completes the response. Turning reservation into outer ASGI
middleware would either run before the caller exists or duplicate authentication. Turning buffering into a route
dependency would allow bytes to reach the client before the durable response commits.

**Decision:** retain the split, rename the pieces around their phases (`reserve_idempotent_request` and
`CompleteIdempotentResponseMiddleware`), and document the request-state handoff. Add cancellation, raised-handler,
multi-chunk response, empty-response, and durable-completion-failure tests. Streaming responses must either be
explicitly rejected for idempotent routes or use a bounded spool; unbounded in-memory buffering is not acceptable.

### The rate limiter implements application policy, not only a generic algorithm

The limiter atomically evaluates several buckets, maintains a conservative local shadow during Redis failure,
distinguishes expensive reads, and exports one combined `RateLimit`/`RateLimit-Policy` decision. A replacement library
is acceptable only if a spike proves all of those contracts, including no partial consumption on rejection and safe
failover across processes.

**Decision:** retain the application-owned policy layer and record a short decision note comparing the pinned
candidate libraries against the test matrix. The Redis sliding-window primitive may be replaced if a maintained
library meets it; the route policy vocabulary and fallback contract remain Squid-owned.

### IP enforcement should be pure ASGI

`RateLimitMiddleware` still subclasses `BaseHTTPMiddleware`. It does not need request-body conveniences and sits on
every request, so a pure ASGI implementation is both narrower and consistent with idempotency. Route-specific
authenticated quotas remain a dependency because they need the matched route and caller.

### Persistence types lag behind domain vocabulary

The application renamed `Principal` to `Caller`, but `idempotency_requests.principal`, its unique constraint, the
rate-limit policy/config key, and explanatory comments preserve the old term. `state` and HTTP method are stored as
untyped strings. The advisory-lock helper accepts an arbitrary namespace string although namespaces are a closed
application set.

**Decision:**

- migrate `principal` to `caller` in the idempotency table and constraint;
- introduce a deprecation window for the environment setting and emitted policy name, reading the old setting but
  warning, then remove it in the next configuration-breaking release;
- model `IdempotencyState` as a `StrEnum`; keep HTTP methods as an allowlisted value because extensions exist and the
  database check is the authoritative unsafe-method set;
- make `AdvisoryLockNamespace` a `StrEnum` and have `lock_uuid` accept it, preventing accidental namespace reuse;
- make `LocalSlidingWindowRateLimiter`, `RedisSlidingWindowRateLimiter`, `DistributedRateLimiter`, and test doubles
  explicitly subclass `RateLimiter` and mark overrides.

### Header rendering is a value contract

The two quota headers duplicate structured string assembly. Introduce one renderer over `RateLimitState` that emits
both fields in stable policy order and validates token-safe policy names. Snapshot the exact header grammar at the
HTTP boundary; do not scatter header fragments across middleware and exceptions.

## Planned work

1. **Pin replay behavior before refactoring.** Add adversarial ASGI tests for reservation/completion ordering,
   cancellation, exceptions, multiple body frames, and completion failure. Decide and document the bounded response
   body limit.
2. **Name the two idempotency phases.** Refactor without changing route declarations, expose one typed request-state
   record, and add a startup assertion that every idempotent route has completion middleware installed.
3. **Make IP rate limiting pure ASGI.** Preserve bypasses, error rendering, state accumulation, and quota headers.
4. **Type implementations and headers.** Subclass the protocol, replace tuple/string test seams with typed records,
   and centralize header serialization.
5. **Migrate caller vocabulary and state.** Add upgrade/downgrade coverage for the column and constraint rename;
   retain compatibility for old configuration for one release.
6. **Close the library question with evidence.** Run the candidate spike against the atomic multi-policy/failover test
   matrix and record why the chosen primitive is kept or replaced.

## Interface sketch

```python
class IdempotencyState(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class AdvisoryLockNamespace(StrEnum):
    SUBMISSION_DRAFT_LIFECYCLE = "submission-draft-lifecycle-v1"
    MEDIA_UPLOAD_REGISTRATION = "media-upload-registration-v1"


class RateLimiter(Protocol):
    async def check(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision: ...


@dataclass(frozen=True, slots=True)
class IdempotencyReservationState:
    service: IdempotencyService
    request: PendingRequest
```

The exact names can change during implementation, but the closed namespace, explicit phase handoff, and protocol
inheritance are acceptance criteria.

## Test matrix

- Unit: local-window eviction/order, multi-policy all-or-nothing, Redis response decoding, fallback/recovery, header
  serialization, protocol implementations, and request-state phase transitions.
- ASGI: anonymous/authenticated identities, route templates rather than concrete URLs, body/query/content-type
  fingerprint differences, successful replay, conflict, handler failure, middleware completion failure, and body cap.
- Redis integration: concurrent requests across two limiter instances and recovery without double-spending the local
  shadow.
- PostgreSQL integration: concurrent key reservation, stale in-progress recovery, encrypted completed responses, and
  caller/state migration upgrade and downgrade.
- Configuration: old key warns and maps exactly; new key wins deterministically if both are supplied.

## Thread dispositions

| Thread | Disposition |
|---|---|
| [`squid/api/idempotency.py`: “don't like this tbh”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796563710) | **Fix/clarify in milestones 1–2.** Keep the necessary phase split, replace implicit request state with a named contract, and bound buffering. |
| [`squid/api/idempotency.py`: “we REALLY should just be a middleware no?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796581124) | **Retain the split with tests.** Reservation needs the authenticated caller; durable completion must remain middleware. |
| [`squid/idempotency/infrastructure/models.py`: “we are not using the word principal”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790727364) | **Fix in milestone 5.** Migrate persisted/configured vocabulary to caller with a compatibility window. |
| [`squid/idempotency/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790746086) | **Fix in milestone 5.** Map durable state through `IdempotencyState`. |
| [`squid/idempotency/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790746250) | **Retain a validated HTTP-method value.** Methods are protocol tokens; add an explicit unsafe-method database check and round-trip tests instead of a misleading closed enum. |
| [`squid/api/rate_limit.py`: “we probably should use a library”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790765273) | **Decide by milestone 6 evidence.** Squid keeps policy/failover ownership; only the Redis primitive is replaceable. |
| [`squid/api/rate_limit.py`: “isn't an ASGI middleware better? what is the problem here?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790775846) | **Fix in milestone 3.** Replace `BaseHTTPMiddleware` for pre-auth IP enforcement; retain matched-route quotas as a dependency. |
| [`squid/api/rate_limit.py`: “subclass the protocol”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790767350) | **Fix in milestone 4.** All implementations explicitly subclass the protocol and use `@override`. |
| [`tests/unit/api/test_rate_limit.py`: “subclass protocol”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790781476) | **Fix in milestone 4.** The scripted limiter becomes a typed `RateLimiter` test double. |
| [`squid/api/rate_limit.py`: “dont like this”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791966825) | **Fix across milestones 3–4.** Separate ASGI transport, policy classification, and backend decision code, each with a typed boundary. |
| [`squid/api/rate_limit.py`: “dont like this being separated strings”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791967918) | **Fix in milestone 4.** Centralize structured quota-header rendering. |
| [`squid/persistence/advisory_locks.py`: “namespace enum?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3803751461) | **Fix in milestone 4.** Use a closed `AdvisoryLockNamespace`. |

## Delivery and rollout

Land behavior-pinning tests first. The middleware refactor is one behavior-preserving commit. The caller rename is a
separate migration commit with downgrade and mixed-configuration coverage. Do not combine the optional library swap
with either refactor: if the spike chooses a library, it gets its own benchmark and rollback point.
