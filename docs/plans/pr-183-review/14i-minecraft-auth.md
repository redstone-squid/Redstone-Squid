# PR #183 Review 14I: Minecraft Authorization

## Scope

This plan covers six threads in the shared backend for Paper installation credentials and player authorization. The
excluded `minecraft/` plugin is not changed. Account identity semantics come from plan 2/14H; HTTP replay and quotas
come from 14B.

## Findings and decisions

### Package errors still use a parallel inheritance convention

`MinecraftAuthorizationError` injects a package code while concrete errors also inherit semantic core errors. The
multiple-inheritance ordering and repeated Pyrefly suppressions differ from other packages.

Have every concrete error inherit its semantic `SquidError` subclass directly and set the shared stable
`ErrorCode`/resource/action fields. If clients require a finer Minecraft reason, add a `MinecraftAuthReason` enum in
`public_context` through a small constructor helper—not a parallel exception hierarchy. Preserve existing wire reason
strings with compatibility tests.

### Manual UUID header parsing protects authentication semantics

FastAPI can parse a `UUID` header directly, but malformed dependency input normally becomes a 422 validation response
while a malformed installation credential should be indistinguishable from missing/invalid credentials (401). Keep
the header value opaque until the authentication dependency, parse it with one credential-token parser, and return
the same authentication error for missing, malformed, or mismatched ID/secret. This intentionally changes today's
public context, where missing/malformed headers are generic but well-shaped mismatches carry a Minecraft reason;
snapshot the distinction first, then make indistinguishable 401 bodies a reviewed security/compatibility change.

The route currently reconstructs a token from two headers and calls `authenticate`. Move that reconstruction/parsing
into `InstallationCredentialService.authenticate_headers` (or a credential value factory) so HTTP code does not know
the token serialization. FastAPI remains responsible only for header aliases. Syntax and length validation stay in
the authentication parser so oversized/malformed credentials cannot become framework-generated 422 responses;
OpenAPI documents the bounds without a pre-dependency validator.

### Clock vocabulary is inconsistent

The application service injects `now: Callable[[], Instant]` while adjacent services call this collaborator `clock`.
Rename the constructor parameter and attribute to `clock`; keep the call shape. UUID/secret factories remain
separately named because they generate identities rather than read time.

### The challenge advisory lock has a real race to explain and a shared helper to use

Creating a challenge counts active rows and then inserts. Without a per-(origin, Java UUID, installation) transaction
lock, concurrent requests can both observe room under `MAX_ACTIVE_CHALLENGES`. State that invariant beside the lock.
Extend 14B's namespaced advisory-lock helper to hash a stable structured key (not only one UUID) and use a typed
`MINECRAFT_ACTIVE_CHALLENGE` namespace. Test two concurrent sessions at the limit.

### Account authorization duplicates account persistence

`PostgresAccountIdentityAuthorizer` queries account/identity tables directly for consent and Java ownership. The
approval query correctly proves current consent plus exact verified Java ownership atomically; splitting it would
create a race. Expose account-owned `can_approve_minecraft_identity(account_id, java_uuid)` with that one-query
semantic. Retain a separate `has_current_consent` operation only for installation registration and CLI authorization.
Move CLI composition/tests in the same milestone or retain a compatibility adapter; do not expose account models.

### API dependencies can be centralized without moving service work into routes

Route-local runtime protocols/getters repeat the application's service bundle shape. Move aliases/getters into
`squid/api/dependencies.py`, following the typed dependencies established in plan 11. Routes retain only auth-specific
composition: headers, caller requirements, request/response DTOs, and operation declarations.

## Planned work

1. **Pin wire behavior.** Snapshot every Minecraft reason/status, missing/malformed header equivalence, token format,
   credential rotation, quota partition, and OpenAPI header constraints.
2. **Align structured errors.** Remove the package exception base, introduce `MinecraftAuthReason`, and preserve wire
   context/status through direct semantic error subclasses.
3. **Move credential assembly into the application boundary.** Keep raw headers at the transport but centralize
   parse/authenticate behavior and indistinguishable failures.
4. **Rename the clock and centralize dependencies.** Mechanical, type-checked changes with no behavior delta.
5. **Reuse the account authorization port.** Move atomic approval plus consent-only queries behind account ownership,
   migrate Minecraft and CLI consumers, then delete the duplicate adapter.
6. **Use a typed namespaced lock.** Generalize the helper, document the count/insert race, and add real PostgreSQL
   concurrency tests.

## Interface sketch

```python
class MinecraftAuthReason(StrEnum):
    INSTALLATION_UNAVAILABLE = "installation_unavailable"
    INVALID_INSTALLATION_CREDENTIAL = "invalid_installation_credential"
    AUTHORIZATION_PENDING = "authorization_pending"
    # Every current wire reason remains represented.


class AccountMinecraftAuthorization(Protocol):
    async def has_current_consent(self, account_id: int) -> bool: ...
    async def can_approve_minecraft_identity(self, account_id: int, java_uuid: UUID) -> bool: ...
```

The advisory-lock helper should accept a namespace plus an already-canonical string/byte key. Typing must preserve
the challenge lock's existing key-byte/SHA-256 derivation and other callers' `hashtextextended` derivation so old and
new processes contend during rollout. A new framing/hash algorithm requires a drained deployment.

## Test matrix

- Errors/API: snapshot today's public-context distinction, then prove the intentional indistinguishable 401 result
  for malformed/missing/wrong/oversized headers; input lengths are bounded inside the auth parser, never by 422.
- Credentials: register, authenticate, rotate, revoke, constant-time digest comparison, old generation fencing, and
  no secret/digest in logs or responses.
- Player flow: Paper/Fabric start, approve, exchange, PKCE, polling interval, expiry, replay, wrong installation,
  grant revocation, and origin binding.
- Account port: consent absent/stale/current, wrong/right Java identity, multiple Java identities, and no account model
  leakage into Minecraft auth.
- PostgreSQL: concurrent challenge creation at `MAX_ACTIVE_CHALLENGES`, lock namespace separation, abandoned/expired
  rows, and rollback releasing the lock.
- Architecture/type checks: API dependencies subclass protocols, services use `clock`, and no direct accounts-table
  imports remain in `squid/minecraft_auth/infrastructure`.

## Thread dispositions

| Thread | Disposition |
|---|---|
| [`squid/minecraft_auth/errors.py`: “??? This is completely different from how other errors are implemented”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791425659) | **Fix in milestone 2.** Use direct semantic core errors plus a typed reason, not a parallel hierarchy. |
| [`squid/api/v1/minecraft_auth.py`: “cant fastapi validate UUID in headers directly”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791507956) | **Retain.** Authentication-owned parsing prevents malformed credentials from becoming 422; milestone 3 centralizes parsing and intentionally unifies 401 context. |
| [`squid/api/v1/minecraft_auth.py`: “why are we doing this”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791510883) | **Fix in milestones 3–4.** Move credential serialization to the application and generic dependency lookup to the shared dependency module. |
| [`squid/minecraft_auth/application/services.py`: “isnt this called clock elsewhere”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791372764) | **Fix in milestone 4.** Rename `now` to `clock` consistently. |
| [`squid/minecraft_auth/infrastructure/repository.py`: “needs a comment on why an advisory lock is needed here.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791404140) | **Fix in milestone 6.** State and test the count-then-insert race under a typed namespace. |
| [`squid/minecraft_auth/infrastructure/accounts.py`: “are we sure this isnt duplicated with another repository”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791382593) | **Fix in milestone 5.** Put atomic approval and consent-only checks behind an account-owned port, migrate the CLI consumer, then delete the duplicate adapter. |

## Delivery and rollout

Pin wire errors before changing inheritance. Error alignment, dependency movement, and clock renaming are separate
commits; header-context unification is an intentional behavior change. The account-port move does not depend on
14H's identity collection rename and includes CLI compatibility. The lock-helper change lands with its concurrency
test while preserving existing lock IDs; changing derivation requires a drained deployment, not merely an atomic
source commit.
