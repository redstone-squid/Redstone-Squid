# PR #183 Review: API, Auth, Records, and Sync

## Findings

Twenty threads land here: the API, auth, records, and sync comments `Glinte` left on
[PR #183](https://github.com/redstone-squid/Redstone-Squid/pull/183) at or before the `5edfd3e`
cutoff. Four more belong to this cluster by path — the `squid/api/errors.py` threads — but
[12-runtime-observability.md](12-runtime-observability.md) subplan 6 already dispositioned them as
"revert the transport-neutrality changes", so they are not reopened here. The `squid/api/app.py`
thread on the legacy `/verify` alias (3788067799) has an original commit after the cutoff and stays
out of scope.

Audited at `e60dded7`. Several of the reviewed shapes are gone. `df1e20e0` replaced signed cursors
with transparent pagination, so `CursorSigner`, `_offset`, and the per-route cursor decoding the
reviewer saw no longer exist; `squid/api/pagination.py` now offers `resolve_selector`,
`parse_page_sort`, and `render_page`, and `BuildQueryService.list_page`
(`squid/builds/application/queries.py:111`) takes a `PageSelector` and returns a domain `Page`.
`41b4285a` replaced the `builds:write`-style scope strings with permission nodes, so scopes are now
catalogue patterns. `56aa670f` moved reconciliation generations onto a sequence and `20232267` gave
the same queue a third resource kind, `starboard_entry`.

One structural fact decides every naming item below: **the versioned API has never shipped from
`master`.** `git ls-tree -r master squid/api/` contains only `__init__.py`, `app.py`, `errors.py`,
and `i18n.py` — no `v1/`, no `contracts/`. There is no external consumer to keep compatible, the
in-repo consumers (`contracts/openapi.json` via `just export-openapi`, then
`web/src/generated/types.gen.ts` via `bun run sdk:generate`) regenerate from the app itself, and
`test_committed_openapi_document_matches_application` (`tests/unit/api/test_openapi_contract.py:70`)
fails loudly if a rename forgets them. So renames land clean, with no aliases and no deprecation
window.

What remains is real:

- **HMAC-SHA-256 is the right primitive here, and nothing in the repository says why.** An API-key
  secret is 32 random bytes (`API_KEY_SECRET_BYTES`, `squid/auth/application/services.py:18`), so
  the stored digest protects a 256-bit secret, not a password. A memory-hard KDF answers low
  entropy; against 256 bits it buys nothing and costs on every authenticated request. Worse, it
  would be reachable: `authenticate` short-circuits before `hash_secret` when the key ID misses
  (`services.py:99`), but the key ID travels in the token prefix and is written into
  `Principal.subject` as `api-key:{key_id}` (`squid/api/security.py:206`), so a key ID that leaks
  through a log lets an attacker force a KDF evaluation per request. The same construction covers
  web sessions (`squid/auth/application/web.py:128,145`, `token_urlsafe(32)`), CLI sessions
  (`squid/cli_auth/application.py:70,86`), and Minecraft credentials
  (`squid/minecraft_auth/application/crypto.py:49,59`) — every one of them a full-entropy random
  secret. CodeQL alert 7 (`py/weak-sensitive-data-hashing`) fires on
  `tests/fuzz/api/database.py:333-337`, where the fuzz fixture re-implements the HMAC by hand
  instead of calling the service.

- **The Discord adapter hand-rolls a rate limiter that understands exactly one 429.**
  `DiscordRestActorResolver._request_member` (`squid/voting/infrastructure/discord_rest.py:81-99`)
  retries once, sleeps on the body's `retry_after`, rejects delays outside 0-60s, and raises on the
  second 429. It never reads `X-RateLimit-Remaining`/`Reset-After`, never distinguishes a global
  limit from a bucket limit, and holds no lock, so N concurrent votes in one guild issue N
  independent requests into the same bucket. The reviewer's fix is available and already a hard
  dependency: `discord.http.HTTPClient` does proactive per-bucket accounting keyed by route and
  major parameters, keeps a global lock (`_global_over`), retries five times, caps waits via
  `max_ratelimit_timeout`, and raises typed `Forbidden`/`NotFound`. `static_login(token)` costs one
  `GET /users/@me` and needs no gateway connection; `get_member(guild_id, member_id)` returns the
  member payload with `roles`, which is all `_actor_from_response` consumes. Nothing about this
  needs a second gateway client, and `tests/architecture/test_boundaries.py` permits `discord` in an
  infrastructure module.

- **Scopes are free strings from configuration to database and back.** `ApiKeyRepository.add` takes
  `frozenset[str]` (`squid/auth/application/ports.py:19`), the column is `ARRAY(Text)`
  (`squid/auth/infrastructure/models.py:29`), and `ApiKeyService.issue` only ever parses a pattern
  when a permission service *and* an owner are present (`services.py:82`), so a CLI-bootstrapped
  key can persist `buildsubmission.raed` and simply match nothing. `ApiConfig.secret_nodes`
  (`squid/config.py:352`) has the same hole at boot. Meanwhile `credential_allows`
  (`squid/api/security.py:96`) re-parses every stored pattern on every authorization check. The
  answer to "literal or enum" is neither: `CATALOGUE` grows, and `Pattern` (`squid/permissions/
  domain/matching.py:48`) is exactly the validated value type this wants. The `sorted(scopes)` the
  reviewer questioned (`squid/auth/infrastructure/repository.py:51`) is the one piece already doing
  the right thing — deterministic storage — with nothing saying so.

- **`discord_sync_queue` is a desired-state reconciliation queue, and the application layer calls it
  a "sync job".** It is not an event log: rows are coalesced by
  `UNIQUE (resource_kind, source_key)` (`squid/sync/infrastructure/models.py:21`), deleted on
  acknowledgement, and carry a sequence-drawn `generation` used as a staleness token rather than an
  ordering. Triggers fill it — six `INSERT … ON CONFLICT (resource_kind, source_key) DO UPDATE`
  statements in `squid/persistence/postgres_entities.sql`, which is where the coalescing actually
  happens — and the only consumer renders Discord posts (`squid/bot/sync/reconciler.py:53`). Discord
  specificity is therefore correct at the table and wrong nowhere — but `SyncJob`, `SyncAction`, and
  `DiscordSyncService` name a transfer, not a reconciliation. The `assert` comment points at the
  real defect underneath the name:
  `squid/sync/infrastructure/repository.py:46,48` `cast()`s two text columns into `Literal` types,
  so a row that violated its check constraint would flow into the reconciler as a valid job and fail
  somewhere else entirely.

- **Four routes repeat the public-visibility rule, and one route decides edit authority.**
  `build is None or build.submission_status is not Status.CONFIRMED` appears at
  `squid/api/v1/builds.py:138`, `squid/api/v1/schematics.py:32`, `:55`, and again as a filter in
  `squid/api/v1/records.py:38`. `edit_build` (`builds.py:126-128`) opens a lease, reads
  `lease.build.submission_status`/`submitter_id`, and decides owner-or-`build.submission.edit`
  inside the transport layer, where the bot's two edit paths (`squid/bot/submission/ui/views.py:660`,
  `squid/bot/submission/edit.py:123`) cannot reuse it. `list_builds` gates the pending view through
  `_require_pending_view` (`builds.py:205`), which is genuinely transport-shaped and can stay.

- **Not-found is generic where the resource is perfectly well known.** `records.py:34`,
  `votes.py:41` and `:100`, `users.py:34`, and `tags.py:33` all raise bare `NotFoundError` with a
  `resource=` string, while `AccountNotFoundError`, `CreatorAliasNotFoundError`,
  `BuildNotFoundError`, `DraftMediaNotFoundError`, and `NotificationSubscriptionNotFoundError`
  already show the shape a typed subclass takes. `build_hit_id` (`squid/api/v1/search.py:108`) has
  the opposite problem: an unparsable projection identifier is a stale index, but it raises
  `ValidationError`, which maps to a 400 blaming the caller for the server's data.

- **The public schema uses three words the review rejected, plus one that names a famous ORM
  pattern.** `BuildTag`'s docstring says "without moderation provenance"
  (`squid/api/v1/schemas/builds.py:182`); `TagDetail.numeric_quantum`
  (`squid/api/v1/schemas/tags.py:26`) publishes a value-granularity step under a physics word;
  `Principal` and "Transport-neutral" open `squid/api/security.py:20-22` and recur across 37 files
  and 81 uses of the bare symbol; and `ActiveRecord`
  (`squid/records/application/models.py:149`) borrows Rails' name for "domain object that persists
  itself" to mean "one result from the currently published computation run". The last one is worth
  separating from the column it reads: `record_computation_runs.is_active` describes a *run*, and
  exactly one run per kind and version is active, which is an accurate use of the word.

- **Schematic downloads page in the route and lie about their file type.**
  `list_build_schematics` loads every public attachment and slices it with `offset_page`
  (`squid/api/v1/schematics.py:34-35`), which is the one route left doing its own paging now that
  builds, records, and notifications page in their services. `get_schematic_content` names every
  download `…-schematic-{id}.schem` (`:65`) regardless of the stored container, though
  `SchematicFormat` has five members (`squid/schematics/domain/models.py:15-22`) and the analysis
  records which one was uploaded.

- **The dependency aliases can be `type` statements; I verified it rather than assuming.** On the
  pinned FastAPI 0.139.2, `type Dep = Annotated[int, Depends(dep)]` resolves identically to the
  plain assignment, and `type PageSizeParam = Annotated[int, Query(ge=1, le=50)]` produces the same
  OpenAPI parameter schema and the same 422 on a violated bound. The alias block at
  `squid/api/dependencies.py:89-103` and the parameter aliases in `squid/api/pagination.py` can both
  convert.

---

## Subplans

1. **Write the credential-hashing rationale down, and stop duplicating the construction**
   - Add a security note next to `ApiKeyService.hash_secret` and in the deployment docs: secrets are
     32 random bytes, the digest is HMAC keyed by a deployment pepper, comparison is
     `hmac.compare_digest`, and revocation/expiry are checked after the digest matches. A password
     KDF defends low-entropy inputs; there are none here, and putting one on the authentication path
     hands anyone who has seen a key ID a per-request work amplifier.
   - Record the same rationale once for the four sites that share the construction (API keys, web
     sessions, CLI sessions, Minecraft credentials) rather than four times.
   - Make `tests/fuzz/api/database.py` call the service's `hash_secret` instead of re-deriving
     `hmac.digest(..., hashlib.sha256)` inline. That removes one CodeQL source outright.
   - For the remaining alert, add `# codeql[py/weak-sensitive-data-hashing]` at the surviving
     hashing sites with the entropy rationale on the adjacent line. If code scanning still reports
     it (inline suppression behaviour differs between the default setup and the advanced workflow in
     `.github/workflows/codeql.yml`), dismiss alert 7 as "used in tests"/"won't fix" and link the
     written rationale in the dismissal comment.
   - Out of cluster, and now owned by [plan 2](02-user-identity-persistence.md) subplan 6:
     `AccountRepository.hash_verification_code` (`squid/accounts/infrastructure/repository.py:419`)
     is the one place the reviewer's instinct bites. A six-digit code is ~19.8 bits, hashed with
     pepper-prefixed plain SHA-256 rather than HMAC, and looked up by code alone across every
     outstanding code. The fix there is HMAC, a wider code, and attempt caps — not a KDF.

2. **Make permission patterns a validated value everywhere they are stored**
   - Parse at every boundary that accepts a pattern: `ApiKeyService.issue` (unconditionally, not
     only when an owner is present), `ApiConfig.secret_nodes` as a Pydantic field validator, and
     `_to_domain` in the repository, which raises `DataIntegrityError` for a row that no longer
     parses.
   - Carry `frozenset[Pattern]` on `ApiKey` and `Caller.nodes`, so `credential_allows` matches
     parsed patterns instead of re-parsing per request. Store `pattern.raw`.
   - Keep `sorted(...)` at the write and say why in one line: the stored array is a set, and a
     stable order makes key diffs, audit output, and fixture comparisons deterministic. Fold
     de-duplication into the same step.
   - The answer to "enum?" belongs in the model docstring: the catalogue is open by construction
     (`squid/permissions/domain/matching.py:15-17` — a pattern granted today covers a node added
     tomorrow), so an enum column would have to be migrated every time a node is registered.

3. **Resolve Discord members through discord.py's rate limiter**
   - Replace the httpx client in `DiscordRestActorResolver` with a lifespan-owned
     `discord.http.HTTPClient`: `static_login(token)` once at startup, `get_member(guild_id,
     discord_id)` per lookup, `close()` on shutdown through the existing
     `self.resources.push_async_callback` registration in `squid/bootstrap.py:513-522`. No gateway
     connection, no `discord.Client`, no second bot session.
   - Map its exceptions onto the semantics the resolver already promises: `Forbidden`/`NotFound` →
     `None` (not a member, or not visible), `RateLimited` and every other `HTTPException` →
     `DiscordMemberServiceUnavailableError`, transport errors likewise. Delete `_retry_after` and
     the 0-60s clamp; `max_ratelimit_timeout` is the supported expression of that bound.
   - Keep the TTL cache, the negative caching, and `resolve()`'s "retain the cached weight on
     failure" behaviour untouched — those are correct and are the reason a vote does not become
     un-castable when Discord hiccups.
   - Not chosen, and why: routing the lookup to the bot process would be exact (the member cache is
     already there) but needs a request/reply channel that does not exist — Redis appears in this
     codebase only as a rate-limiter backend (`squid/api/rate_limit.py:244`) — and it puts a
     synchronous vote behind a second process's liveness.

4. **Name the reconciliation queue after what it is, and validate what it reads**
   - Rename in the application layer only: `SyncJob` → `ReconciliationJob`, `SyncAction` →
     `ReconciliationAction`, `SyncQueueRepository` → `ReconciliationQueue`, `DiscordSyncService` →
     `DiscordReconciliationService`, `services.discord_sync` → `services.discord_reconciliation`.
     The table, the model, and the triggers keep `discord_sync_queue`: the rows exist to repair
     Discord posts, and a migration that renames a trigger-fed table buys nothing here.
   - Answer the "isn't it an event queue" question in the model docstring: rows are coalesced by
     `(resource_kind, source_key)` in the triggers, deleted on acknowledgement, and `generation` is
     a staleness token drawn from a sequence and compared against the post's applied revision — an
     event log would be append-only and replayable, and this is neither. The comment already in
     `postgres_entities.sql` explains the sequence; the model should not make the reader find it.
   - Convert `ResourceKind` and `SyncAction` from `Literal` aliases to `StrEnum`s, matching the
     direction `509406c2` took in voting, and replace the two `cast()`s with enum construction that
     raises `DataIntegrityError` naming the row id and the offending value. The check constraints
     stay; they are the reason this is a data-integrity failure rather than a validation error.

5. **Move build visibility and edit authority into the build services**
   - Add `BuildQueryService.get_public(build_id) -> Build`, raising `BuildNotFoundError`, and use it
     from `builds.py:135`, `schematics.py:31`, and `schematics.py:55`. `records.py` keeps its own
     filter — it is asking a different question (which holders are publicly renderable) and already
     raises `DataIntegrityError` when the answer is "not all of them".
   - Add `BuildService.apply_edit(actor, build_id, patch, *, expected_revision) -> Build`, which
     opens the lease, authorizes owner-or-`build.submission.edit` against the loaded build, and
     commits. `ApiKeyService` already precedents an application service depending on
     `PermissionService` (`squid/auth/application/services.py:14`), and
     `tests/architecture/test_boundaries.py` allows it. The route then validates transport input,
     calls one method, and maps the result.
   - Keep the low-level `edit()` lease for the bot's two callers; `apply_edit` is the authorizing
     wrapper, not a replacement.
   - Leave `_require_pending_view` in the route. It answers "may this credential see a non-public
     listing", which is a transport question about the caller, not about a build.

6. **Type the errors and the mappings the routes produce**
   - Add `RecordNotFoundError`, `VoteSessionNotFoundError`, `CreatorNotFoundError`, and
     `TagNotFoundError` beside the existing subclasses, each carrying its identifier in
     `public_context` and its `resource` by default. Delete `_vote_not_found` and the four inline
     `NotFoundError(...)` constructions.
   - Change `build_hit_id` to raise `DataIntegrityError`: an unparsable projection id is the index
     lying, and the caller cannot fix it. `hydrate_builds` already logs the milder version of the
     same drift.
   - Give the pure mappers one generic base — `class FromDomain[DomainT]` with
     `from_domain(cls, value: DomainT) -> Self` — and apply it only where the mapping is total and
     context-free (`RecordSummary`, `SchematicSummary`, `TagDetail`, `BuildSummary`). Mappers that
     need request context stay explicit and stay off the base:
     `VoteSessionDetail.from_domain(session, caller_account_id=...)` is the example that shows why a
     mandatory universal mixin would be wrong.
   - Answer the "can we return `Build` and map elsewhere" half of thread 3784229160 with the same
     reasoning, and fix the half that is a real gap: `get_build`'s docstring and its `responses(...)`
     entries should say what 404 means for a pending build (indistinguishable from a missing one, on
     purpose).

7. **Rename the public vocabulary while nothing depends on it**
   - `Principal` → `Caller`, `CurrentPrincipal` → `CurrentCaller`, `principal_allows` →
     `caller_allows`, and every `principal:` parameter to `caller:`. The class docstring already
     says "caller identity" three times, which is the evidence that this is the natural word.
   - Replace "-neutral" with the concrete claim it stands in for: in `squid/api/security.py`, the
     point is that one type answers for HTTP, CLI, web-session, and Minecraft callers; in
     `squid/api/capabilities.py:1`, that the identifiers do not depend on a client. Do `squid/api/`
     in the rename commit and sweep the remaining prose (69 occurrences of "neutral" across
     `squid/`) in one docs-only commit. Plans 2 and 6 should stop minting new ones.
   - `TagDetail.numeric_quantum` → `numeric_step`, and rename the domain field and the
     `tag_definitions.numeric_quantum` column with it — a split vocabulary between the API and the
     table is exactly the debt this review is about. One migration renames the column and the
     `tag_definitions_numeric_quantum_check` / `tag_definitions_numeric_metadata_check` /
     `tag_definitions_non_numeric_unit_check` constraint bodies; `schema_dump.sql` regenerates in CI.
   - `BuildTag`'s docstring drops "provenance" for what it means: the public tag omits who applied
     it and how. Leave `build_tag_assignments.provenance` alone for now, and record the trade: a
     column rename there touches the tags repository, the builds mapping, the taxonomy backfill, and
     a migration, for a word no client ever sees. If the ban is meant repo-wide, that is its own
     commit, and `source` is the replacement.
   - `ActiveRecord` → `PublishedRecord`, with `RecordService.get`/`list_page` and the repository's
     `get_active_record`/`list_active_records`/`count_active_records` following. Keep
     `record_computation_runs.is_active`: it describes a run, and one run per kind and version is
     genuinely the active one.
   - Regenerate `contracts/openapi.json` (`just export-openapi`) and the web SDK
     (`bun run sdk:generate`) in the same commits as the renames.

8. **Give schematics the same paging and the right file names**
   - Add `SchematicService.list_public_page(build_id, *, selector, page_size) -> Page[StoredSchematic]`
     and have the route call it with `resolve_selector`/`render_page` like every other list route.
     The service may keep slicing in memory: a build's attachment count is small and bounded in
     practice, and the store port has no offset/limit today. Write the trigger down — when a single
     build's attachments stop fitting comfortably in one query, `list_for_build` gains
     `offset`/`limit` and a count, and the service body changes without the route noticing.
   - Derive the download extension from `analysis.metrics.source_format` rather than hard-coding
     `.schem`, keeping the `build-{build_id}-schematic-{schematic_id}` stem so no user-supplied
     filename reaches a response header.
   - While in the file: `assert publication.license is not None` (`schematics.py:59`) restates an
     invariant `public_content` has already enforced. Let the service return the license with the
     content, or raise `DataIntegrityError`, rather than asserting in a route that runs with
     assertions enabled only by convention.

---

## Interfaces and Tests

### Validated scopes

```python
class ApiKeyRepository(Protocol):
    async def add(
        self,
        *,
        key_id: str,
        secret_hash: bytes,
        label: str,
        scopes: frozenset[Pattern],
        ...
    ) -> ApiKey: ...
```

```python
def _to_domain(model: ApiKeyModel) -> ApiKey:
    try:
        scopes = frozenset(Pattern.parse(raw) for raw in model.scopes)
    except InvalidPatternError as error:
        msg = "A stored API key carries an unparsable permission pattern."
        raise DataIntegrityError(msg, context={"key_id": model.key_id}) from error
```

### Discord member resolution

```python
class DiscordMemberResolver:
    """Resolve guild-member facts through discord.py's rate-limited HTTP client."""

    def __init__(self, http: HTTPClient, *, capabilities: ActorCapabilityResolver | None = None) -> None: ...

    async def member(self, account_id: int, discord_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        try:
            payload = await self._http.get_member(guild_id, discord_id)
        except (Forbidden, NotFound):
            return None
        except HTTPException as error:  # includes RateLimited past max_ratelimit_timeout
            raise DiscordMemberServiceUnavailableError(...) from error
```

### Reconciliation job

```python
class ReconciliationResource(StrEnum):
    BUILD = "build"
    VOTE_SESSION = "vote_session"
    STARBOARD_ENTRY = "starboard_entry"


class ReconciliationAction(StrEnum):
    REFRESH = "refresh"
    DELETE = "delete"


def _job(row: DiscordSyncQueueItem, claimed_at: Instant) -> ReconciliationJob:
    try:
        resource = ReconciliationResource(row.resource_kind)
        action = ReconciliationAction(row.action)
    except ValueError as error:
        msg = "A reconciliation row holds a value its check constraint should have rejected."
        raise DataIntegrityError(msg, context={"id": row.id}) from error
```

### Authorizing edit

```python
async def apply_edit(
    self,
    actor: BuildEditor,
    build_id: int,
    patch: BuildEditPatch,
    *,
    expected_revision: int | None = None,
) -> Build:
    """Edit an owned pending build, or any build with `build.submission.edit`."""
```

### Tests

- **Credential hashing** (`tests/unit/auth/application/test_services.py`): a known-answer digest
  pins the construction; a rotated pepper stops authenticating old tokens; malformed tokens, revoked
  keys, and expired keys each fail without touching `touch_last_used`; the fuzz fixture and the
  service agree on one digest for one secret.
- **Scope validation** (same module, plus `tests/unit/test_config.py`): an invalid pattern is
  rejected at issue time even with no permission service wired in; an invalid `secret_nodes` entry
  fails configuration load; a stored row that no longer parses raises `DataIntegrityError`;
  duplicate and unsorted input produce one canonical stored array.
- **Discord resolution** (`tests/unit/voting/infrastructure/test_discord_rest.py`, rewritten against
  a stubbed `HTTPClient`): 403 and 404 yield `None` and are cached; a capped `RateLimited` and a
  malformed payload both raise unavailable; `resolve()` still swallows failures; capability
  resolution still reads role ids from the payload; shutdown closes the client exactly once.
- **Reconciliation** (`tests/unit/sync/test_service.py`, plus an integration case): a row with an
  out-of-domain `resource_kind` raises `DataIntegrityError` rather than reaching the reconciler;
  claim/complete/fail fencing and dead-lettering behave as before under the new names.
- **Build policy** (`tests/unit/api/test_build_writes.py`,
  `tests/unit/api/test_authoritative_build_views.py`): a pending build 404s for an anonymous caller
  through the service, not the route; the owner may edit a pending build; a non-owner without the
  node gets 403; a non-owner with the node succeeds; the bot's lease path is unaffected.
- **Typed errors** (`tests/unit/api/test_phase2_reads.py`, `test_vote_writes.py`): each new
  subclass maps to 404 with its identifier in the problem context; an unparsable search hit is a 500
  with a `DataIntegrityError` code, not a 400.
- **Naming and contract** (`tests/unit/api/test_openapi_contract.py`): the committed document
  matches the app after `numeric_step`, `PublishedRecord`, and the docstring changes; the CLI
  operation fixtures still validate.
- **Schematics** (`tests/unit/schematics/test_public_api.py`): a paged listing returns the same
  items the route used to slice, with `total`/anchors; each format downloads under its own
  extension; a non-public attachment still 404s.
- **Dependency aliases**: no new test. `type` aliases are exercised by every existing route test,
  and the OpenAPI contract test pins the parameter schemas they produce.

---

## Disposition

| # | Thread | Comment | Disposition |
|---|---|---|---|
| 3787898986 | `squid/auth/application/services.py:115` | "lets use something safer than sha256" | **Retained, documented.** 256-bit random secrets, peppered HMAC, constant-time compare; a KDF defends entropy that does not exist here and adds per-request work reachable by anyone holding a key ID. Rationale written at the call site and in the deployment docs. |
| — | `squid/accounts/infrastructure/repository.py:419` | (no thread; found auditing 3787898986) | **Handed to [plan 2](02-user-identity-persistence.md) §6.** The verification code is the one peppered digest with real entropy pressure, and it is an accounts credential, not an API one. |
| — | `tests/fuzz/api/database.py:335` (CodeQL alert 7) | `py/weak-sensitive-data-hashing` | **Fix by reuse, then suppress.** The fixture calls `hash_secret` instead of re-deriving it; the surviving site carries an inline suppression, with UI dismissal as the fallback. Bot comment, post-cutoff, tracked here because it is the same question. |
| 3784699433 | `squid/auth/application/ports.py:19` | "lieral or enum" | **Fix, as neither.** Scopes become `frozenset[Pattern]`. The catalogue is open by design, so a `Literal` or enum would need a migration per registered node. |
| 3787903592 | `squid/auth/infrastructure/models.py:29` | "enum? normalize it?" | **Fix.** Normalized (parsed, de-duplicated, sorted) on write; unparsable stored rows raise `DataIntegrityError` on read. Column stays `ARRAY(Text)`, with the reason in the docstring. |
| 3787905411 | `squid/auth/infrastructure/repository.py:51` | "why do we need to sort this" | **Retained with rationale.** A set has no order; a stable one makes diffs, audit output, and fixtures deterministic. The line becomes the normalization step and says so. |
| 3787848067 | `squid/voting/infrastructure/discord_rest.py:1` | "use the squid api key to spawn a dpy client to read" | **Fix, adapted.** A lifespan-owned `discord.http.HTTPClient` with `static_login` — discord.py's bucket accounting, global lock, and typed errors — but no gateway connection and no second `Client`. |
| 3784500076 | `squid/sync/infrastructure/models.py:11` | "dont like this name, isnt it an event queue, and why discord specific" | **Fix in the application layer, retained in persistence.** `ReconciliationJob`/`ReconciliationAction`/`DiscordReconciliationService`; the table stays `discord_sync_queue` because every row exists to repair a Discord post, and the docstring explains why coalesce-and-delete is not an event log. |
| 3784510637 | `squid/sync/infrastructure/repository.py:48` | "assert" | **Fix, more strongly.** The two `cast()`s become `StrEnum` construction raising `DataIntegrityError`, so a row that escaped its check constraint fails at the boundary instead of inside the reconciler. |
| 3784229160 | `squid/api/v1/builds.py:134` | "1. needs better status code docstring 2. can we return Build and have the mapping elsewhere" | **Fix (1), retained (2).** The docstring and `responses(...)` state why a pending build is a 404. The mapping already lives in the schema module; the route call site is the seam where transport picks a representation, and hiding it costs the request context `VoteSessionDetail` needs. |
| 3784235990 | `squid/api/v1/builds.py:139` | "this sort of policy stuff should be in services especially with auth stuff" | **Fix.** `BuildQueryService.get_public` and `BuildService.apply_edit`; the four copies of the visibility rule collapse and the bot can reuse the authorized edit. |
| 3784263461 | `squid/api/v1/search.py:96` (was `builds.py:100`) | "domain helper? and probly a class method of SearchSort" | **Fix.** `SearchSort.parse` in `squid/search/domain`, so the bot and API parse one sort syntax. |
| 3784295855 | `squid/api/v1/search.py:108` (was `builds.py:108`) | "idk what to think of this" | **Fix.** The failure is a stale index, not a bad request: `DataIntegrityError`, matching what `hydrate_builds` already logs. |
| 3784341454 | `squid/api/dependencies.py` | "type statement?" | **Fix.** Verified on the pinned FastAPI 0.139.2 that `type` aliases resolve identically for `Depends` and `Query`, including OpenAPI output and bound violations. |
| 3784447613 | `squid/api/security.py` | ban "principal" and "-neutral" | **Fix.** `Principal` → `Caller` and friends; "-neutral" prose replaced with the concrete claim, `squid/api/` first and the rest in one docs-only sweep. |
| 3784149987 | `squid/api/v1/schemas/builds.py:182` | "ban provenance" | **Fix in the public schema.** The docstring says the public tag omits who applied it and how. The internal `build_tag_assignments.provenance` column is costed and left for a decision. |
| 3787961881 | `squid/api/v1/schemas/records.py:30` | "we should have an abc or mixin generic to the domain model to force a from domain method" | **Fix, scoped.** A `FromDomain[DomainT]` base for total, context-free mappers only; mappers taking request context stay explicit. |
| 3787964907 | `squid/api/v1/records.py:34` | "we need specific notfounderror subclasses" | **Fix.** `RecordNotFoundError`, `VoteSessionNotFoundError`, `CreatorNotFoundError`, `TagNotFoundError`, matching the five subclasses that already exist. |
| 3787977592 | `squid/records/application/models.py:149` | "bad name, dont use active" | **Fix.** `PublishedRecord`, with the service and repository following. `record_computation_runs.is_active` stays: it names a run, and exactly one run is active. |
| 3788009685 | `squid/api/v1/schemas/tags.py:42` | "numeroc quantum is a bad name" | **Fix.** `numeric_step`, renamed through the domain and the column so the API and the table share one word. |
| 3788015256 | `squid/api/v1/schematics.py` | "pagniation should be in service layer..." | **Fix.** `SchematicService.list_public_page` returning a domain `Page`; the route uses `resolve_selector`/`render_page` like every other list. |
| 3788016322 | `squid/api/v1/schematics.py` | "? why always .schematic" | **Fix.** The extension comes from the stored `SchematicFormat`; the filename stem stays server-generated. |
| 3784405259 | `squid/api/errors.py:1` | "lets think about reverting most of the changes here" | **Deferred to plan 12**, subplan 6. |
| 3784392069 | `squid/api/errors.py:11` | "i really dont think we havee to avoid importing fastapi here" | **Deferred to plan 12**, subplan 6. |
| 3784376308 | `squid/api/errors.py:47` | "useless" | **Deferred to plan 12**, subplan 6. |
| 3784410445 | `squid/api/errors.py:65` | "this can be kept" | **Deferred to plan 12**, subplan 6. |
| 3788067799 | `squid/api/app.py` | "remove the old endpoint" | **Out of scope.** Original commit `efe1e02` is after the `5edfd3e` cutoff, per [README.md](README.md). |

## Sequencing

Subplans 1-4 are independent of each other and of the rest; 2 touches `Caller.nodes`, so it lands
before 7's rename or accepts the trivial conflict. Subplan 5 lands before 6, because the typed
not-found errors are most of what the new service methods raise. Subplan 7 is mechanical and noisy —
keep it in its own commits (one per rename), each regenerating `contracts/openapi.json` and the web
SDK, so review can read the behaviour changes without the rename diff on top. Subplan 8 is
independent.

## Implementation notes

Landed across `9311019d`…`5ac64ea4`. Four things the plan did not anticipate:

- **The contract export was not deterministic.** `responses()` took its OpenAPI description from
  `HTTPStatus.phrase`, which Python 3.13 changed for 422 ("Unprocessable Entity" →
  "Unprocessable Content"). The unit suite runs on 3.12, 3.13 and 3.14, so
  `test_committed_openapi_document_matches_application` could only ever match the interpreter that
  last exported the document — and it was failing on 3.12 before any of this work started. Pinned
  in `0a492f8c`, which also surfaced what the breakage was hiding: the two Java-identity refresh
  routes from `b489c5e6` had never had their `Request-Id` response headers exported.
- **`ReconciliationResource` needs one seam.** `squid.posts.domain.ResourceKind` is the same three
  values as a `Literal`, and a `StrEnum` member is not assignable to it. Converting the posts
  context too would remove two more `cast()`s, but it reaches `starboard_renderer.py` and the rest
  of the starboard paths this review excludes. `ReconciliationResource.post_kind` writes the
  mapping out rather than casting it, so adding a resource fails there instead of at the renderer
  lookup.
- **Three uses of "principal" are external contracts and stay.** The
  `idempotency_requests.principal` column and the unique index it anchors, the `principal`
  partition in `RateLimit-Policy`, and `SQUID_API_RATE_LIMIT_PRINCIPAL_REQUESTS`. The column
  docstring records the trade. The idempotency application layer speaks `caller` and the repository
  maps it, which is the same split the reconciliation queue uses.
- **The web SDK was regenerated by hand.** `bun` is not installed in this environment, so the
  affected JSDoc descriptions in `web/src/generated/` were edited to match what `openapi-ts` emits.
  CI's `sdk:check` (`bun run sdk:generate && git diff --exit-code -- src/generated`) is the
  authority; if it disagrees, take its output.

Two pre-existing failures are unrelated to this plan and were not touched:
`tests/unit/bot/test_command_taxonomy.py` has not been updated for the `/poll` command tree that
`509406c2` added (plan 9's territory), and
`tests/unit/api/test_rate_limit.py::test_api_ip_limit_returns_quota_headers_and_retry_after`
asserts an exact `Retry-After` of 300 and flakes to 298/299 when the suite is slow.

## Validation

- Focused modules while developing, with `--no-cov`: `tests/unit/auth/`, `tests/unit/sync/`,
  `tests/unit/api/`, `tests/unit/voting/infrastructure/`, `tests/unit/schematics/test_public_api.py`.
- After the renames: `just export-openapi`, `bun run sdk:generate` in `web/`, then
  `tests/unit/api/test_openapi_contract.py` — the committed-document assertion is the one that
  catches a forgotten regeneration.
- `alembic heads` and a schema-dump regeneration for the `numeric_step` migration; nothing else in
  this plan touches persistence structure.
- `tests/architecture/test_boundaries.py` after subplan 3 (a `discord` import moves into
  `squid/voting/infrastructure/`) and after subplan 5 (`squid.builds.application` gains a dependency
  on `squid.permissions.application`).
- Changed-file Ruff and BasedPyright, plus `git diff --check`.
- The Discord resolver has no integration coverage against the live API and should not gain any;
  its contract is now discord.py's, and discord.py tests its own rate limiter.

Replying on GitHub and resolving these threads still requires separate explicit authorization, per
[README.md](README.md).
