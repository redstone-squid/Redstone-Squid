# A REST API for Redstone-Squid

> **Status.** Design approved. The four prerequisite findings are resolved; the remaining Phase 0
> configuration and import-surface work is the next unit of work.
> The blockers in "Findings" were pre-existing and verified in-tree rather than hypothetical
> risks, and three would have surfaced as production incidents with the matching route.
> Amend this document in place as phases land, calling out where building it proved part
> of it wrong rather than silently rewriting.

## Context

Redstone-Squid has a rich domain — 66 tables across 15 bounded contexts, a search engine with a
safe query language, computed record competitions, schematic analysis, and weighted voting — but
all of it is reachable only through Discord. The HTTP surface (`squid/api/`, four files) is a
single-endpoint webhook: `GET /health` and `POST /verify`, the latter guarded by a raw `!=`
compare against one shared secret.

We want a real REST API serving three consumers: a public community/web frontend, Minecraft server
plugins, and internal admin tooling. The write surface is deliberately narrower than the bot's:
verification, build submission, build edit, and voting. Confirm/deny, starboard configuration, and
server settings stay Discord-only.

The outcome is a documented, versioned, contract-tested HTTP surface that reuses the existing
application services rather than growing a parallel data path.

## What already exists and must be reused

- **`squid/runtime.py::ApplicationServices`** — a frozen dataclass of 19 services. `get_services`
  (`squid/api/app.py:30`) already hands all of them to any route. Transports never touch
  repositories; `tests/architecture/test_boundaries.py` enforces this.
- **`squid/api/errors.py`** — RFC 9457 `application/problem+json`, locale-aware, with redaction and
  `X-Error-ID` correlation. Genuinely good; it needs declaring in OpenAPI, not replacing.
- **`squid/core/errors.py::ErrorCode`** — a stable enum that is already the public error
  vocabulary. New failure modes get enum members, not ad-hoc strings.
- **The search context** — `squid/search/application/parser.py` (safe Lucene-subset grammar with
  positional errors and suggestions), `fields.py::DEFAULT_FIELD_REGISTRY`,
  `cursor.py::CursorCodec` (HMAC-signed opaque cursors), `infrastructure/compiler.py`.
- **`squid/api/i18n.py`** — `Accept-Language` negotiation, already applied to every error response.
- **`tests/unit/api/test_openapi_contract.py`** — a schemathesis 4.x harness whose docstring
  already asks every new route to be registered.

## Findings — resolved prerequisites

Verified in-tree during design and fixed before adding routes. The security/error fixes landed in
`5bbdaf1`; the dead-query and recency fixes landed in `0fbe837`.

1. **Resolved — anonymous pending-build disclosure.** The caller's `visible_statuses` policy is now
   applied independently of the parsed query and included in the cursor binding. Previously,
   `squid/search/infrastructure/repository.py:386` used:

   ```python
   def _requires_confirmed_default(scope, query) -> bool:
       return scope in {SearchScope.BUILDS, SearchScope.ALL} and not _references_field(query, "status")
   ```

   The confirmed-only default *disabled itself* when the query mentioned `status`, while
   `projection.py:362` indexed a `status` facet for every build regardless of state. Piping raw
   user `q=` into this would have given anonymous callers `?q=status:pending` over unreviewed
   submissions.

2. **Resolved — three bare `ValueError`s became 500s.** `InvalidCursorError(ValueError)`
   (`squid/search/application/cursor.py:14`), `SearchRequest.__post_init__`'s page-size check
   (`squid/search/domain/models.py:53`), and `SearchService.suggest`'s limit check were not
   `SquidError`s, so `_status_for_error` could not see them and `handle_unexpected_error` returned 500.

3. **Resolved — `get_outdated_messages` was dead *and* broken.** The service, port, repository
   method, and managed PostgreSQL function have been removed. It previously
   joined on `messages.submission_id`/`builds.submission_id` and compared
   `messages.last_updated < builds.last_update` — none of those four columns exist (the live schema
   has `messages.build_id`, `messages.updated_at`, `builds.id`, `builds.edited_time`). It had zero
   callers, which is why nobody noticed.

4. **Resolved — no recency sort existed.** `created_at` and `updated_at` are now sortable timestamp
   fields, projected from `builds.submission_time` and `builds.edited_time`; migration
   `a4c8e2f6b913` re-enqueues existing builds for backfill.

Additionally, `CursorCodec` is seeded with `secrets.token_bytes(32)` per process
(`squid/bootstrap.py:154`), so cursors are invalid across the bot/API process split, across
restarts, and across replicas.

## Layout

```
squid/api/
  app.py            # factory, middleware, OpenAPI metadata
  dependencies.py   # services, principal, pagination params, locale
  security.py       # Principal, Scope, auth schemes, require()
  pagination.py     # Page[T] envelope
  errors.py         # existing + responses() helper for OpenAPI
  i18n.py           # existing
  v1/
    __init__.py     # router assembly + tag metadata
    builds.py  records.py  search.py  tags.py  versions.py
    schematics.py  users.py  votes.py  admin.py
    schemas/        # pydantic DTOs, one module per resource
squid/auth/         # NEW context: api keys, web sessions, oauth state
squid/sync/         # NEW context: discord reconciliation queue
```

`squid/auth/` and `squid/sync/` are bounded contexts, not `squid/api/` subpackages, because
`tests/architecture/test_boundaries.py:57` forbids `squid.api*` from importing
`squid.*.infrastructure*`. They reach the API through `ApplicationServices`.

## The six abstractions

1. **Two representations per resource** — `XSummary` (collections) and `XDetail` (item GET). No
   `?expand=` or sparse fieldsets; fixed shapes keep the OpenAPI document honest and
   schemathesis-testable. Sub-resources carry the rest.

2. **One pagination envelope** — `Page[T] = {items, next_cursor, has_more}` on every collection.
   Extract `squid/core/pagination.py::SignedCursor` (HMAC over an arbitrary payload plus a binding
   hash); `CursorCodec` becomes one caller rather than being contorted to express keyset positions
   it has no `SearchScope` for. The signing key moves to `RuntimeConfig` — not `ApiConfig` —
   because `CursorCodec` is built in `create_application_services`, which both processes call.

3. **One *matching* grammar, which is not the authorization boundary.** Collection endpoints take
   `q=` (the existing Lucene subset), `sort`, `page_size`, and `cursor` instead of inventing
   per-resource query parameters, and `GET /v1/search/fields` publishes the registry so UIs
   self-configure. Visibility is enforced *below* the grammar, as a `SearchRequest` field the
   transport sets and the caller cannot:

   ```python
   visible_statuses: frozenset[str] | None = None   # None = backend default
   ```

   ANDed unconditionally in `squid/search/infrastructure/repository.py`, taking precedence over
   `_requires_confirmed_default`. `status:denied` ANDed with `status IN ('confirmed')` is empty —
   un-bypassable, unlike query-string rewriting. This forces a change inside `squid/search/`; own
   it there rather than papering over it in the router.

4. **One `Principal`** — `kind: anonymous|service|user`, `subject`, `scopes`, `discord_id`,
   `user_id` — from a single `Depends(current_principal)`, with declarative
   `Depends(require(Scope.BUILDS_WRITE))`. Scopes bound what the *credential* may do;
   `AuthorizationService.is_global_administrator` bounds what the *human* may do; effective
   permission is the intersection. A service key has no `discord_id` and so can never be an admin
   by identity — which keeps a leaked CI key a nuisance rather than a takeover.

5. **A DTO visibility policy** — pydantic DTOs with `from_domain()`, never domain dataclasses on
   the wire. Three specific redactions:
   - `VoteSessionSnapshot.votes` is `Mapping[user_id, float]` and `.selections` carries per-voter
     `user_id`. Serializing it naively breaks ballot secrecy on `anonymous_live` and
     `anonymous_hidden` polls. Response DTOs are visibility-aware: aggregates only, except the
     caller's own selection.
   - Expose `creator_aliases.name` (a credit string from build metadata) but never the
     `users` ↔ `creator_aliases` linkage, `discord_id`, or `minecraft_uuid`. The disclosure is
     "this alias belongs to an account", not the name itself.
   - `Build.extra_info` is allowlisted, never dumped (it holds submitter free text), and
     `messages.content` is never exposed.

6. **Errors stay RFC 9457** — add a `responses()` helper so every route declares `ProblemDetail` in
   its OpenAPI schema, and add `INVALID_CURSOR`, `INVALID_QUERY`, and `RATE_LIMITED` to
   `ErrorCode`. Routes raise `AuthenticationError`/`AuthorizationError`, never bare
   `HTTPException`, because `handle_http_error` collapses everything non-404 to `INVALID_REQUEST`.

## Resource model

```
GET    /v1/builds                    ?q= | ?status=
GET    /v1/builds/{id}
POST   /v1/builds                    DoorSubmissionInput
PATCH  /v1/builds/{id}               BuildEditPatch
GET    /v1/builds/{id}/schematics
GET    /v1/schematics/{sha256}/content
GET    /v1/records  /v1/records/{id}
GET    /v1/search   /v1/search/fields  /v1/search/suggest
GET    /v1/tags  /v1/tags/{id}  /v1/versions
GET    /v1/vote-sessions/{id}
POST   /v1/vote-sessions/{id}/votes
GET    /v1/users/me  POST /v1/users/me/consent
POST   /v1/verify                    (existing, moved under /v1, alias kept)
```

## Decision: collection reads use two paths

`GET /v1/builds?q=` goes through `SearchService` (eventually consistent, documented as such);
`GET /v1/builds/{id}`, `?status=pending`, and `/v1/users/me/builds` go through the authoritative
repository. Search-only fails on freshness (a just-POSTed build is absent until the bot's 30s loop
runs, and absent indefinitely if the bot is down), authorization (finding 1), and ordering
(finding 4).

Search results are **hydrated**, not served directly: `BuildSearchHit` carries only
`source_id`/`title`/`status`/`description`/`score`/`tags`, so serving it would produce a second,
drifting summary shape — and `document_data` is a projection snapshot containing submitter free
text. Collect hit ids, call `get_many(ids)`, render one `BuildSummary` in hit order. Hits whose
build has vanished are dropped **and logged**; that log is the projection-staleness alarm.

Two narrow additions to `BuildQueryRepository` (`squid/builds/application/queries.py:22`):

```python
async def get_many(self, build_ids: Sequence[int]) -> list[Build]: ...
async def list_page(self, *, statuses: frozenset[Status], submitter_id: int | None,
                    after_id: int | None, limit: int) -> list[Build]: ...
```

Keyset on `id DESC`. No arbitrary predicates — the authoritative path offers *identity and status*,
the search path offers *matching*. That split is what keeps "one grammar" honest.

Also add `created_at`/`updated_at` to `DEFAULT_FIELD_REGISTRY` with `supports_sort=True`, emit them
as facets in `_build_projection`, and backfill via an enqueue migration (precedent:
`alembic/versions/2026_07_30_1500-c9b2d861f540`).

## Decision: voting over HTTP uses a REST member resolver

`cast_vote` needs a `VoteActor` whose weight derives from Discord guild roles, resolvable today
only from a `discord.Member` (`squid/bot/voting/vote.py:305`). The API process has no gateway.

Flat weight 1.0 is wrong: `_calculate_refresh` recomputes every selection's weight through
`RoleVoteWeightPolicy` on the next refresh and would overwrite it, and it enfranchises users the
policy returns `None` for — privilege escalation by transport. Proxying writes to the bot process
makes the bot a single point of failure for API writes and adds a whole transport; the two
processes share nothing but Postgres.

Instead: `squid/voting/infrastructure/discord_rest.py::DiscordRestActorResolver`, roughly 40 lines
of `httpx` against `discord.com/api/v10` `GET /guilds/{g}/members/{u}`, which returns exactly the
`roles` list the policy needs. Not discord.py — `discord.Client` starts a gateway and
`discord.http.HTTPClient` is private. Wire it in `bootstrap.py` via `set_actor_resolver`.

Two entry points, because the port contract says `resolve() -> VoteActor | None` and
`_calculate_refresh` reads `None` as "retain cached weight": `resolve()` swallows everything and
returns `None`; a public `member()` raises `ServiceUnavailableError` on 5xx/timeout and returns
`None` on 403/404. Routes call `member()`, so "not in that guild" (403) is distinguishable from
"Discord is down" (503). Cache `(guild_id, user_id)` for 300s; honor 429 `retry_after`.
`is_staff`/`is_trusted` are `False` for API principals — those flags gate only `close()` and
`delete_log` eligibility, neither of which is in scope. Staff powers stay Discord-only.

The resource is keyed by session id, not `message_id` (a Discord artifact; `snapshot.messages` is a
tuple, and generic polls may have none). One service method, no repository change, no migration:

```python
# squid/voting/application/services.py
async def cast_vote_by_session(
    self, vote_session_id: int, actor: VoteActor, option_id: str
) -> CastVoteResult:
    snapshot = await self._repository.get_by_id(vote_session_id)
    if snapshot is None:
        return CastVoteResult(session=None, rejection="not_found")
    message = next((m for m in snapshot.messages if m.guild_id == actor.guild_id), None)
    if message is None:
        return CastVoteResult(session=snapshot, rejection="wrong_guild")
    option = next(
        (o for o in snapshot.options_for_guild(message.guild_id) if o.identifier == option_id), None
    )
    if option is None:
        return CastVoteResult(session=snapshot, rejection="invalid_option")
    return await self.cast_vote(message.id, actor, option.emoji)
```

The HTTP contract takes `option_id`, not `emoji` — `VoteOption.__post_init__` guarantees
`identifier` is non-empty, and per-guild emoji aliasing should not be a client concern.

Abuse controls: `Principal.kind == "user"` only (**service keys must never hold `votes:cast`** —
one credential, unlimited ballots); membership proven by the bot-token lookup, not OAuth `guilds`
claims (which give guild list, not roles, and go stale); weight only from `RoleVoteWeightPolicy`,
with `None` becoming a 403, so HTTP voting is never *more* permissive than reacting in Discord;
rate limit roughly 30 writes per 5 minutes. `cast_vote` is already upsert-shaped, so idempotency
is free.

## Decision: reconciliation uses an outbox table filled by triggers

If the API mutates a build or a vote, the Discord message rendering it goes stale and nothing
repairs it. `get_outdated_messages` is broken (finding 3) and wrong for the job anyway: it covers
only confirmed builds, only build messages, is per-`server_id` so the drainer must already know the
guild list, and carries no notion of *what* changed. LISTEN/NOTIFY is not durable — `NOTIFY` is
lost with no listener connected, and a vote closing must not be dropped across a bot restart.

Mirror `search_projection_queue`, the pattern already proven in this repo:

```sql
CREATE TABLE discord_sync_queue (
    id            bigserial PRIMARY KEY,
    resource_kind text NOT NULL CHECK (resource_kind IN ('build', 'vote_session')),
    source_key    text NOT NULL,
    action        text NOT NULL CHECK (action IN ('refresh', 'delete')),
    enqueued_at   timestamptz NOT NULL DEFAULT now(),
    claimed_at    timestamptz,
    attempts      integer NOT NULL DEFAULT 0,
    last_error    text
);
CREATE UNIQUE INDEX discord_sync_queue_pending
    ON discord_sync_queue (resource_kind, source_key, action) WHERE claimed_at IS NULL;
```

The partial unique index plus `ON CONFLICT DO NOTHING` coalesces to at most one pending job per
resource, which is exactly right for re-render semantics.

**Filled by DB triggers, not application code** — matching the precedent at
`alembic/versions/2026_07_30_1330-d42be8a917c3_enqueue_search_projection_refresh.py`. The decisive
argument: the API then needs zero knowledge that Discord exists, any write through any transport
enqueues automatically, and `squid.api*` never imports anything Discord-shaped. Triggers go on
`builds` (AFTER UPDATE of rendered columns and `submission_status`) and on `vote_sessions`/`votes`.

Owner: `squid/sync/` — `DiscordSyncService.claim(limit)`/`complete(id)`/`fail(id, error)` over
`DiscordSyncQueueRepository`, exposed as `ApplicationServices.discord_sync`. Consumer:
`squid/bot/sync/reconciler.py`, a `@tasks.loop(seconds=15)` copied structurally from
`squid/bot/submission/records.py:235` (same try/except-log, same
`before_loop: await self.bot.wait_until_ready()`), reusing
`GenericVoteSession(...).update_messages()`. `discord.NotFound` means
`MessageService.untrack(message_id)` and complete the job; `attempts` drives exponential skip, then
drop with a logged error.

Fold the search-projection drain into this same loop and document that it runs only in the bot
process — `refresh_search_index` is wired into `ApplicationServices` but its only caller is a bot
cog, so an API-only deployment currently has a permanently frozen index.

The same migration drops `get_outdated_messages` and deletes `MessageService.get_outdated` and
`MessageRepository.get_outdated_messages`.

## Decision: API keys and Discord OAuth2

Token format `sq_<key_id>_<secret>`, 32 CSPRNG bytes of secret. `key_id` is split out of the token
so lookup is an indexed equality followed by a constant-time verify; hashing the whole opaque token
forces either a table scan or an unsalted fast hash.

```sql
CREATE TABLE api_keys (
    id bigserial PRIMARY KEY,
    key_id text NOT NULL UNIQUE, secret_hash bytea NOT NULL,
    label text NOT NULL, scopes text[] NOT NULL DEFAULT '{}',
    owner_user_id bigint REFERENCES users(id) ON DELETE SET NULL,
    created_by bigint REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz, revoked_at timestamptz,
    last_used_at timestamptz, last_used_ip inet
);
CREATE INDEX api_keys_active ON api_keys (key_id) WHERE revoked_at IS NULL;
```

**HMAC-SHA256 with a config pepper, not Argon2.** The secret is 256 bits of CSPRNG entropy — there
is nothing to brute-force, so a memory-hard KDF buys nothing and costs latency on every request.
This also reuses the existing precedent exactly (`verification_code_pepper`,
`squid/bootstrap.py:163`) rather than inventing a second hashing scheme. Throttle `last_used_at`
writes to once per 60s per key to avoid write amplification.

OAuth2: **Discord authorization code with PKCE, exchanged server-side, with the API issuing its own
opaque session cookie.** The callback lands on the API, not the SPA, because the token exchange
needs `client_secret`; PKCE stays anyway to protect the code in the redirect leg. Opaque cookie
rather than JWT because instant revocation is needed for bans, logout, and
`CURRENT_CONSENT_VERSION` bumps — a JWT denylist is a session table with extra steps, and the
lookup cost equals the API-key path, giving **one code path for both credential types**. Request
scope `identify` only and **do not store Discord access or refresh tokens**: identity is needed
once (`GET /users/@me` → `id`), and role facts come from the bot token, which is more accurate.

```sql
CREATE TABLE web_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash bytea NOT NULL UNIQUE,
    user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz, user_agent text
);
CREATE TABLE oauth_states (
    state text PRIMARY KEY, code_verifier text NOT NULL, redirect_to text,
    created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL
);
```

`oauth_states` lives in Postgres, not process memory — the callback may hit a different replica
than the authorize call. Cookie `__Host-squid_session`, `HttpOnly; Secure; SameSite=Lax; Path=/`.
Because cookies are ambient, unsafe methods need a double-submit CSRF token; API-key requests use
`Authorization` and are immune — a reason to keep the two credential types on separate transports.
Validate `redirect_to` against the CORS allowlist or this is an open redirect.

**Consent gate**: if the account lacks `UserConsent` at `CURRENT_CONSENT_VERSION` and was created
after `CONSENT_CUTOFF`, issue the session anyway but mark it `consent_pending` and gate *writes*,
not login. Writes return `ErrorCode.CONSENT_REQUIRED` pointing at `POST /v1/users/me/consent`;
reads still work. That is what the `CONSENT_CUTOFF` grandfathering is for.

Config (`squid/config.py`) — mind `env_nested_max_split=1`, so only one nesting level resolves:

```python
class OAuthConfig(_FrozenModel):        # SQUID_OAUTH_DISCORD_CLIENT_ID -> oauth.discord_client_id
    discord_client_id: str | None = None
    discord_client_secret: SecretStr | None = None
    redirect_uri: AnyHttpUrl | None = None
    session_ttl_hours: int = Field(default=336, ge=1)

class ApiConfig(_FrozenModel):          # add
    key_pepper: SecretStr
    session_pepper: SecretStr
    cors_origins: tuple[str, ...] = ()
    bot_token: SecretStr | None = None
```

`cursor_secret` goes on `RuntimeConfig` (both processes build it); update the `include={...}` sets
in `ApplicationConfig.bot_process()` and `.api_process()`. Keep the legacy `ApiConfig.secret` as a
bootstrap credential mapped to a synthetic all-scopes principal, deprecated —
`tests/unit/api/fakes.py` and the schemathesis harness depend on it.

## Phases

Ordering is dependency-forced: error mapping before anything schemathesis touches, reconciliation
before any write route, user auth before votes.

- **Phase 0 — unbreak, no new routes.** Map `InvalidCursorError`, `QuerySyntaxError`, and the two
  page-size/limit `ValueError`s to `ValidationError`. Add the three `ErrorCode` members. Move
  `cursor_secret` to `RuntimeConfig`. Drop the broken `get_outdated_messages` and its dead service
  methods. Relax `tests/architecture/test_import_surfaces.py:10` to assert *intent*
  (`squid.api.errors` does not drag in `sqlalchemy`/`discord`) rather than pinning importer names
  that churn on every router PR.
- **Phase 1 — thinnest useful vertical slice.** `GET /v1/builds/{id}`, `GET /v1/builds?q=`,
  `GET /v1/search/fields`. Anonymous-only `Principal`, `Page[T]`, `BuildSummary`/`BuildDetail` with
  hydration, the un-overridable `visible_statuses` filter, `created_at`/`updated_at` fields plus
  backfill, CORS, OpenAPI metadata. No writes, no auth storage. Exercises all six abstractions
  against a real consumer and ships independently.
- **Phase 2 — remaining reads.** Records, tags, versions, public alias view, schematic metadata,
  `GET /v1/schematics/{sha256}/content` (stream the bytea), vote-sessions read with
  visibility-aware DTOs.
- **Phase 3 — API keys.** `squid/auth/`, migration, `Principal(kind="service")`, `require(Scope)`,
  rate limiting. `/verify` moves under `/v1` behind a scope; the old path stays as an alias so the
  Minecraft plugin keeps working.
- **Phase 4 — reconciliation.** `discord_sync_queue`, triggers, `squid/sync/`, and the bot cog
  (which also drains the projection queue). **Must land before Phase 6.**
- **Phase 5 — OAuth2.** `Principal(kind="user")`, consent gate, `GET /v1/users/me`.
- **Phase 6 — build writes.** `POST /v1/builds` mapping to `DoorSubmissionInput`, rejecting
  non-door categories with a typed 400 in the router rather than letting `BuildRepository.save`
  raise "Only doors are supported for now". `PATCH` maps to `BuildEditPatch` under the
  `BuildEditLease` context manager; `BUILD_BUSY` becomes 409, already mapped.
- **Phase 7 — voting writes.** `DiscordRestActorResolver`, `cast_vote_by_session`,
  `POST /v1/vote-sessions/{id}/votes`.

## Critical files

| Path | Change |
|---|---|
| `squid/search/infrastructure/repository.py:386` | `visible_statuses`, replacing the self-disabling default |
| `squid/search/application/fields.py:111` | add sortable `created_at`/`updated_at` |
| `squid/search/infrastructure/projection.py:362` | emit the new recency facets |
| `squid/bootstrap.py:154` | cursor secret from config; wire `squid/auth/`, `squid/sync/`, actor resolver |
| `squid/config.py` | `OAuthConfig`, `ApiConfig` additions, `RuntimeConfig.cursor_secret`, process include-sets |
| `squid/builds/application/queries.py:22` | `get_many`, `list_page` |
| `squid/voting/application/services.py` | `cast_vote_by_session` |
| `squid/api/app.py`, `squid/api/errors.py` | app factory, middleware, `responses()` helper |
| `squid/core/errors.py:12` | three new `ErrorCode` members |
| `tests/architecture/test_import_surfaces.py:10` | assert intent, not importer names |
| `tests/unit/api/test_openapi_contract.py` | every new route, every phase |

New: `squid/api/{dependencies,security,pagination}.py`, `squid/api/v1/**`,
`squid/core/pagination.py`, `squid/auth/**`, `squid/sync/**`,
`squid/voting/infrastructure/discord_rest.py`, `squid/bot/sync/reconciler.py`, plus Alembic
revisions on head `7f2c9d4e6a81`.

## Verification

Per phase, smallest-first per `AGENTS.md`:

- `pytest tests/unit/api tests/architecture --no-cov` on every change — the architecture tests are
  the layering guard and will catch an `squid.api` → infrastructure import immediately.
- `pytest tests/unit/search --no-cov` for the `visible_statuses` and field-registry work, plus a
  new regression test asserting `?q=status:pending` returns nothing for an anonymous principal.
  That test is the point of Phases 0 and 1 and should be written first.
- The schemathesis harness (`tests/unit/api/test_openapi_contract.py`) grows with each phase; its
  `not_a_server_error` check is what proves the Phase 0 error mapping actually holds once query
  parameters exist.
- `alembic heads` after each migration (single head expected), and
  `squid/persistence/alembic_entities.py`'s function and trigger counts updated — it asserts
  exactly 12 functions and 23 triggers, so dropping `get_outdated_messages` and adding sync
  triggers both move those numbers.
- `git diff --check`, then ruff and pyrefly over changed files only.
- End-to-end for Phase 1: `python app.py`, then `curl localhost:8000/v1/builds?q=piston` and
  `curl localhost:8000/openapi.json | jq '.info, .components.securitySchemes'`.
- Phases 4 and 7 need a live Discord guild: submit a build via `POST /v1/builds` and confirm the
  bot's reconciler re-renders the message within roughly 15s; cast a vote over HTTP and confirm the
  weight matches what the same user's reaction would produce.
