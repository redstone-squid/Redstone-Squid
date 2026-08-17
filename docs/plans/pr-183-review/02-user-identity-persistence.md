# PR #183 Review: User Identity and Persistence

## Scope

Eight review comments on [PR #183](https://github.com/redstone-squid/Redstone-Squid/pull/183) at or before the
`5edfd3e` cutoff:

| Thread | Anchor | Comment |
|---|---|---|
| 3765927329 | `builds/infrastructure/repository.py` | "duplicating info, not sure if good idea or not" |
| 3766109224 | `users/application/services.py` | "unrelated but this means we need to handle name change too" |
| 3766114978 | `users/domain/models.py` | "don't have so many normalize functions, this is utils" |
| 3766128577 | `users/infrastructure/models.py` | "todo: see if this gets upgraded into a full account later" |
| 3766140695 | `users/infrastructure/repository.py` | "we need to do something about these excessive mappings" |
| 3766202899 | `users/errors.py` | "todo: needs changing when we go outside discord" |
| 3775414045 | `permissions/application/services.py` | "what a service..." |
| 3775418269 | `permissions/infrastructure/models.py` | "should become an user id later" |

Thread 3766207128 (`AliasAlreadyClaimedError` conflict context) belongs to
[plan 01](01-consent-verification-ux.md) and 3766220759 to [plan 13](13-test-tooling-dispositions.md).
`squid/users` no longer exists in git, so the provider-neutral rename has already landed.

One item here has no thread of its own. [Plan 11](11-api-auth-records-sync.md) answered
"lets use something safer than sha256" (3787898986) for API keys by retaining HMAC-SHA-256, and its
audit of the other peppered-digest sites found one that does not fit that answer: the Java
verification code. It is an accounts credential, so it is planned here rather than there.

## Findings

### Creator-name normalization is computed twice, and the two results disagree

`creator_aliases.normalized_name` is a persisted generated column, `lower(btrim(name))`
(`squid/accounts/infrastructure/models.py:129`), carrying the unique index that gives a creator credit its identity.
Six call sites build the comparison value in Python instead, via `normalize_ign(name) -> name.strip().lower()`
(`squid/accounts/domain/models.py:18`).

Measured against Postgres 17 under `en_US.utf8`:

| input | Python `.strip().lower()` | SQL `lower(btrim(...))` | agree |
|---|---|---|---|
| `ΣΣ` | `σς` (final sigma) | `σσ` | ✗ |
| `İ` | `i̇` (2 codepoints) | `i` (1 codepoint) | ✗ |
| ` Foo ` | `foo` | ` foo ` | ✗ |
| `\tFoo\t` | `foo` | `\tfoo\t` | ✗ |
| `Straße` | `straße` | `straße` | ✓ |

Postgres `lower()` is collation-dependent and `btrim()` strips only U+0020, so no Python implementation can be
guaranteed equivalent. The consequences are live, not theoretical:

- **A repeat submission crashes.** In `_get_or_create_aliases` (`squid/builds/infrastructure/repository.py:501`),
  crediting `ΣΣ` a second time: the `SELECT` on `normalized_name = 'σς'` misses, the `INSERT` conflicts on the
  database's `σσ`, `on_conflict_do_nothing` returns no row, and the fallback `.scalar_one()` raises `NoResultFound`.
- **Claims silently fail.** `get_alias_by_name`, `claim_unclaimed_alias`, `request_claim`, and the alias claim inside
  `consume_code_and_link_account` all miss such a row, so `/account claim ΣΣ` reports "creator name not found"
  against an alias that exists.
- **A third variant exists.** `SuggestionRepository.creators` (`squid/suggestions/infrastructure/repository.py:236`)
  uses `query.strip().casefold()`, which differs from both (`ß`→`ss`, `ΣΣ`→`σσ`).

The whitespace rows are latent rather than live, because every write path pre-applies Python `.strip()` before
insert. The `lower()` rows bite today.

Nothing normalizes an *account* name; accounts have no name column, and `account_identities.display_name` is
presentational and never compared. Creator aliases are the only normalized thing, and normalization is genuinely
required there: it is what makes a build crediting `Bob` and one crediting `bob` the same creator.

### Which folding wins, and why not both

The two foldings are **incomparable** — they disagree about *what collides*, in both directions:

| pair | SQL `lower(btrim())` collides | Python `NFKC + casefold` collides |
|---|---|---|
| `Strasse` / `Straße` | no | yes |
| `I` / `İ` | yes | no |
| `ΣΣ` / `Σς` | no | yes |

So keeping one column of each is not a hedge. With both unique, the table rejects the union of both collision sets;
with one decorative, that column groups creators differently from the one defining identity, and any SQL consumer
joining on it silently disagrees with the application about who a creator is — the same class of bug, relocated.

The application wins the tie, and the portability argument runs opposite to intuition. `pg_database.datcollversion`
is the host's **glibc version**: Postgres tracks it because a libc upgrade changes `lower()` and invalidates indexes
built on it. The SQL column is pinned to an OS upgraded outside our control, whereas `NFKC` and `casefold` are
Unicode-standard operations with equivalents in Rust, Go, and Java, pinned to a CPython version we choose. Casefold
is also simply the better fold: it is the Unicode operation *for* caseless matching, correctly unifying `ΣΣ`/`Σς`
where `lower` does not, and NFKC-then-strip catches NBSP and ideographic space before trimming.

### Rename behavior exists only as a side effect of relinking

`consume_code_and_link_account` (`squid/accounts/infrastructure/repository.py:362`) overwrites `display_name` and
opportunistically claims the alias matching the new IGN, but:

- It only claims a **pre-existing** alias, so a renamed user whose new name no build credits yet gets no creator
  credit at all.
- A new name held by another account matches nothing and returns `claimed_alias=None`, indistinguishable from
  "nothing to claim". The collision is invisible to the caller and to staff.
- There is no other trigger. `MinecraftPlayerContext` (`squid/minecraft_auth/domain/models.py:159`) carries
  `java_uuid` but no username, so Minecraft-side authentication cannot observe a rename. The verification-code flow
  is the only place a fresh Mojang name enters the system.
- Previously claimed aliases stay attached, which is the behavior we want, but incidentally rather than by policy.

### Provider neutrality stops short of the transport boundary

`Principal` (`squid/api/security.py:21`) carries `account_id` for `account`, `cli`, and `minecraft_player` kinds, but
`squid/api/v1/me.py:35` and `:51` reject any principal whose `discord_id` is `None`. A CLI-device or Minecraft-player
caller holding a valid `account_id` gets a 401 on its own account. `AccountService` is keyed on `discord_id`
throughout, and `AccountAlreadyLinkedError.__init__` takes `discord_id` positionally.

### Per-row queries in the account repository

- `create()` issues one `session.refresh(row)` per identity in a loop (`repository.py:115`).
- `_load_account` (`repository.py:505`) issues one identity query per account and there is no batched loader, so any
  multi-account read is an N+1. Plan 01 needs exactly this: presenting claimants in `/account claims` requires
  loading identities for N accounts.
- The explicit `_to_identity` / `_to_account` / `_to_alias` / `_to_claim` mappers are correct and stay. SQLAlchemy
  cannot populate frozen domain dataclasses without coupling the domain to persistence.

### The verification code is the one credential where the digest question has teeth

`generate_verification_code` mints `secrets.randbelow(900_000) + 100_000` (`squid/bootstrap.py:532`) — 900 000
values, about 19.8 bits — and `hash_verification_code` stores `sha256(pepper || code)`
(`squid/accounts/infrastructure/repository.py:417-419`), a pepper-prefixed plain digest rather than an HMAC. Every
other secret in this codebase is 32 random bytes behind `hmac.digest(...)`, which is why plan 11 retains that
construction and rejects a KDF for API keys. This site is the exception, and the digest is the least of it:

- **The lookup is keyed on the code alone.** `consume_code_and_link_account` selects on `expires > now`, `valid`,
  and `code = hash(...)` (`repository.py:430-437`); it never mentions the Minecraft UUID the code was issued for.
  One guess is therefore tested against *every* outstanding code at once, so the chance per attempt is
  `outstanding / 900 000` rather than `1 / 900 000`.
- **A hit links someone else's identity to the guesser.** The Discord ID comes from the caller of
  `/account link <code>` (`squid/bot/verify.py:60`), not from the code, so a successful guess attaches the matched
  Java account to the attacker's Discord account. That is an identity-takeover primitive, not a nuisance.
- **Nothing caps attempts.** Codes live ten minutes and are single-use
  (`squid/accounts/infrastructure/models.py:235-244`), which bounds the window but not the rate; the only limiter
  on `/account link` is Discord's own command throughput.

### Already addressed after `5edfd3e`

- The polymorphic row upgrade is superseded by `account_identities` plus stable `accounts.public_creator_id`.
- `permission_grants` and `permission_role_assignments` already key on `subject_account_id`.
- `PermissionService` is 262 lines of cohesive methods over `PermissionStore` and `SubjectRuleCache`.

## Subplans

1. **One folding, owned by the application and written by the ORM**
   - Replace `normalize_ign` with `fold_creator_name(name)` in `squid/accounts/domain/models.py`:
     `unicodedata.normalize("NFKC", name).strip().casefold()`. Update the re-export in
     `squid/accounts/domain/__init__.py`.
   - `creator_aliases.normalized_name` stops being generated and becomes a plain `NOT NULL` column carrying an
     `insert_default` derived from `name`, so no insert path can skip it — this fires for ORM flushes, Core
     `insert()`, `pg_insert(...).on_conflict_do_nothing`, and executemany alike. Keep the unique constraint and the
     `text_pattern_ops` prefix index untouched.
   - A column-level `onupdate` cannot be used: it fires for every UPDATE of the row, including the claim updates that
     never mention `name`, where the callable has no parameter to read. Use a `before_update` ORM event conditional
     on the `name` attribute actually being dirty.
   - Add `CheckConstraint("normalized_name = btrim(normalized_name) AND normalized_name !~ '[A-Z]'")`. Both
     conditions hold for any casefold output, so there is no false-positive risk, and together they catch a raw SQL
     write that stored the display spelling verbatim.
   - Migration off head `b1c2d3e4f5a7`: `ALTER COLUMN normalized_name DROP EXPRESSION` degenerates the column in
     place, keeping its data and every index built on it, then re-fold existing rows through Python and add the
     check. The downgrade restores the generated column and is allowed to fail loudly, since names that casefold
     together cannot both survive the regenerated unique index.
   - Convert the six comparison sites to `fold_creator_name(...)`, including `SuggestionRepository.creators`, which
     drops its third `casefold()` variant.
   - *This direction also removes a risk the SQL one carried.* Folding in Python keeps the typeahead predicate a
     plain `normalized_name LIKE 'prefix%'`, which the planner can bound directly; a SQL-side fold would have made it
     `LIKE lower(btrim($1)) || '%'`, whose index usage depended on the planner folding the prefix first.

2. **One atomic Java rename reconcile**
   - Add an `IdentityRefresh` domain value reporting the previous and current name, the alias claimed, the alias
     names retained, and any contested alias with the staff claim it opened.
   - Add `AccountRepository.refresh_java_identity(*, account_id, java_uuid, username)` running in one transaction:
     lock the account, load its `JAVA` identity for `java_uuid`, capture the previous name, write the new
     `display_name` and `verified_at`, then reconcile the alias for the new name `FOR UPDATE`.
   - Alias reconciliation is total over four cases: absent → insert and claim `VERIFIED_IGN`; unclaimed → claim
     `VERIFIED_IGN`; held by this account → no write; held by another account → **never transfer**, insert a pending
     `CreatorAliasClaim` (the `creator_alias_claims_one_pending_per_account` partial unique index makes this an
     upsert) and return it for staff review.
   - Previously claimed aliases need no write; they are already attached. Report their names so the UX can say the
     user is still credited under the old name.
   - Extract the reconcile body as `_reconcile_java_name(session, ...)` and call it from **both**
     `refresh_java_identity` and `consume_code_and_link_account`, inside the existing transaction. This is the point
     of the subplan: link and refresh share one policy instead of scattering rename behavior across callers.
   - Extend `resolve_claim` with `reassign: bool = False`. Approving a claim on a held alias currently raises
     `AliasAlreadyClaimedError`; a contested rename needs staff to be able to move it. Surface `reassign` explicitly
     on `/account approve-claim` so a transfer can never happen by accident.

3. **Service and entry points, keyed on account**
   - `AccountService.refresh_java_identity(account_id, *, java_uuid=None)` loads the account, picks its Java
     identity, calls `MojangClient.get_username`, raises `MinecraftAccountNotFoundError` on `None`, then delegates.
     It can also raise `MinecraftServiceUnavailableError`, which every caller must render.
   - Give each `discord_id`-keyed service method an `account_id`-keyed core with the Discord form as a thin wrapper,
     so the bot keeps its convenience and other transports stop being second-class.
   - Discord: `/account refresh`, plus `/account refresh user:<member>` for staff. Render every `IdentityRefresh`
     branch through `t(locale, _(...))` — unchanged, renamed and claimed, renamed and contested (naming the pending
     claim), and old names retained.
   - API: `POST /v1/users/me/minecraft/refresh` keyed on `principal.account_id`, and
     `POST /v1/accounts/{account_id}/minecraft/refresh` for staff. Both take `enforce_route_rate_limits` and
     `enforce_request_idempotency`, since they reach an external service.
   - Fix `me.py` to gate on `principal.account_id` rather than `principal.discord_id`.
   - There is no first-party CLI in this repository; `squid/cli_auth` is device authorization for an external client
     that calls the REST API. CLI exposure is therefore satisfied by the route accepting a `cli` principal, and no
     CLI command is written here.
   - Add `ACCOUNT_IDENTITY_REFRESH` (`NodeScope.GLOBAL`, `default=Default.ALLOW`) and
     `ACCOUNT_IDENTITY_REFRESH_ANY` (`Tag.MODERATION`) beside the existing `ACCOUNT_*` nodes.

4. **Provider-neutral errors**
   - `AccountNotFoundError`, `ConsentRequiredError`, and `AccountAlreadyLinkedError` accept a `(provider, subject)`
     pair in their context, keeping `discord_id` as an accepted keyword for the Discord entry points.
     `AccountAlreadyLinkedError`'s positional `discord_id` becomes keyword-only.
   - Keep Discord-specific user-facing strings only where a Discord entry point raises them; add neutral variants for
     the API paths. No new `ErrorCode` values.

5. **Mapping and batch-loading audit**
   - Replace the per-row `session.refresh(row)` loop in `create()` with one `flush()` and direct construction from
     values already in hand.
   - Add `_load_accounts(session, models)` doing a single `WHERE account_id IN (...)` query with grouping, expose
     `get_many(account_ids)` on the repository and port, and rewrite `_load_account` as its single-model case.
   - Give `pending_claims()` an optional batched claimant load so plan 01's claimant presentation does not
     reintroduce an N+1.
   - Retain the explicit mappers and add no relationships.

6. **Verification codes: keyed digest, wider code, capped attempts**
   - `hash_verification_code` becomes `hmac.digest(pepper, code, hashlib.sha256)`. The pepper is a key, and
     prefix-SHA-256 is the weaker construction for no saving. No dual-read path and no backfill: codes expire in ten
     minutes, so a deploy invalidates at most one window, and `/link` reissues.
   - Widen the code to nine or ten digits (`secrets.randbelow(9 * 10**9) + 10**9` is about 33 bits). **Stay
     numeric**: `/verify` returns `int` and that endpoint is the one part of this API already on `master`
     (`git show master:squid/api/app.py`), consumed by the in-game plugin that shows the code to the player. A
     base32 code like `squid/cli_auth/application.py:75` mints would be stronger still and would change the
     response type, so it is not worth it here — 33 bits against a ten-minute window is already decisive.
   - Cap attempts on the consuming side, which is where the guessing happens: count consecutive failures per
     Discord account, refuse after a small number for a cooling-off period, and log the refusal. The window and
     single-use flag stay as they are.
   - Keying the lookup by Minecraft UUID as well as by code is *not* available: the Discord user typing the code
     knows nothing else, and the code is the whole binding. Entropy and attempt caps are the levers.

## Interfaces and Tests

### Normalization

```python
def fold_creator_name(name: str) -> str:
    """Return the comparison form of a creator name.

    NFKC first, so compatibility forms unify and NBSP or ideographic space become U+0020
    before trimming; then strip; then casefold, the Unicode operation for caseless matching
    that `str.lower()` is not — `lower` leaves `ΣΣ` as `σς` while casefold gives `σσ`.

    This is the only definition of the value, and it is deliberately not reproduced in SQL.
    """
    return unicodedata.normalize("NFKC", name).strip().casefold()
```

The stored column derives from it without any caller's help:

```python
def _fold_from_name(context: DefaultExecutionContext) -> str:
    return fold_creator_name(context.get_current_parameters()["name"])

normalized_name: Mapped[str] = mapped_column(
    Text, nullable=False, insert_default=_fold_from_name, init=False
)

@event.listens_for(CreatorAlias, "before_update")
def _refold_on_name_change(_mapper, _connection, target: CreatorAlias) -> None:
    if get_history(target, "name").has_changes():
        target.normalized_name = fold_creator_name(target.name)
```

### Rename outcome

```python
@dataclass(frozen=True, slots=True)
class IdentityRefresh:
    """Outcome of reconciling a Java identity's display name with its creator credit."""

    account_id: int
    java_uuid: UUID
    current_name: str
    previous_name: str | None = None
    claimed_alias: CreatorAlias | None = None
    retained_alias_names: tuple[str, ...] = ()
    contested_alias: CreatorAlias | None = None
    opened_claim: AliasClaim | None = None

    @property
    def renamed(self) -> bool:
        return self.previous_name is not None and self.previous_name != self.current_name

    @property
    def is_contested(self) -> bool:
        return self.contested_alias is not None
```

`VerificationLinkResult` carries the whole `refresh` alongside the `claimed_alias` the original
link flow needed, and `AliasClaim` grows an optional `claimant` that `pending_claims(with_claimants=True)`
fills from one batched load — the shape plan 01's claimant presentation needs.

### Tests

- **Golden corpus** (`tests/unit/accounts/test_creator_name_folding.py`): the exact fold of the curated set. Nothing
  in the database can check the fold, so a CPython upgrade that changes casefolding has to fail here instead of
  silently regrouping creators. Do not pin `unicodedata.unidata_version`: `requires-python` spans 3.12 and 3.13,
  which ship Unicode 15.0.0 and 15.1.0 respectively, and the corpus is stable across both.
- **The ORM writes the fold** (`tests/integration/accounts/`): ORM flush, `pg_insert(...).on_conflict_do_nothing`,
  and executemany all store the folded value; `before_update` re-folds a corrected display spelling; and the check
  constraint rejects a raw `INSERT` that stored the name verbatim.
- **Crash regression**: a build crediting the same folding-sensitive name *twice* resolves to one alias instead of
  raising `NoResultFound`. The repeat matters — two *different* spellings do not reproduce it, because Postgres
  folds them apart and the insert never conflicts. Verified failing before the fix.
- **Collision semantics**: the two sigma spellings are one creator and reachable by either; dotted capital I and
  ASCII I stay two.
- **Typeahead** (`tests/integration/suggestions/`): prefix matching across case and compatibility forms, `%`/`_`
  escaped rather than honoured, and an `EXPLAIN` assertion that the `text_pattern_ops` prefix index is still chosen.
- **Rename matrix** (integration): unchanged name; new name unclaimed; new name absent (row created and claimed);
  new name held by another account (no transfer, pending claim opened, old aliases retained); relinking the same
  UUID twice; concurrent refresh of one account yielding one winner and no duplicate claim rows.
- **Reassignment**: `resolve_claim(reassign=False)` still raises `AliasAlreadyClaimedError` on a held alias;
  `reassign=True` moves it and records `STAFF_APPROVED`.
- **Query counts**: `create()` and `get_many()` issue a constant number of statements regardless of identity or
  account count, counted through `before_cursor_execute`.
- **Transport neutrality**: `/v1/users/me` and the refresh route succeed for a `cli` principal whose `discord_id` is
  `None`.
- **Bot rendering**: every `IdentityRefresh` branch, plus `MinecraftServiceUnavailableError`, produces a localized
  message.
- **Verification codes**: a known-answer HMAC digest; a code issued for one Java account cannot be consumed after
  its expiry or twice; the generated range is the widened one; consecutive wrong codes from one Discord account hit
  the cap and the next correct code is refused until the cooling-off passes; `/verify` still returns an `int`.

### Before merging

`alembic heads` must show the single new revision. The migration degenerates the generated column in place
(`ALTER COLUMN ... DROP EXPRESSION`) rather than dropping it, so the unique constraint and the prefix index survive
untouched and only the stored values are rewritten.

Both directions are already covered: `test_migrations_create_schema_without_drift` walks head down past this
revision and back up, running `alembic check` at each end, so no separate round-trip test is needed. The downgrade
is deliberately allowed to fail on data where two names collide under casefold but not under `lower(btrim(...))`;
there is no correct automatic answer, and failing beats silently discarding a creator credit.

There is no deployment yet, so there is no production data to survey; the re-fold pass in the migration is written
correctly regardless, since it is the same pass a later Unicode change would need.

## Disposition

| Thread | Disposition |
|---|---|
| 3765927329 — duplicating info | **Fix.** The duplication is not merely redundant: the two computations disagree, in both directions, and crash a repeat submission. `fold_creator_name` is now the only definition, written into the column by an ORM default so no insert path can skip it. |
| 3766109224 — handle name change | **Fix.** `refresh_java_identity` gives the rename an explicit atomic policy — refresh on link and on demand, retain old claimed aliases, create and claim an unheld new name, never transfer a held one. Background Mojang polling stays deferred. |
| 3766114978 — too many normalize functions | **Fix.** Resolved by deletion rather than by a utils module: `normalize_ign`, the SQL generated column, and the suggestions `casefold()` variant collapse into one `fold_creator_name`. |
| 3766128577 — row upgrade | **Already fixed.** `account_identities` plus `accounts.public_creator_id` superseded it, and `squid/users` is gone from git. Verify and close. |
| 3766140695 — excessive mappings | **Retain, with cleanup.** Explicit mapping stays; SQLAlchemy cannot populate frozen domain dataclasses without coupling the domain to persistence. The actionable part — per-row queries — is fixed by `get_many` and by removing the `refresh` loop. |
| 3766202899 — outside Discord | **Fix.** Errors carry `(provider, subject)`, services gain `account_id`-keyed cores, and `me.py` stops rejecting non-Discord principals that hold an `account_id`. |
| 3775414045 — "what a service..." | **Retain, with rationale.** 262 lines of cohesive methods over `PermissionStore` and `SubjectRuleCache`; splitting it would scatter one authorization decision. |
| 3775418269 — should become a user id | **Already fixed.** `permission_grants` and `permission_role_assignments` key on `subject_account_id`. Verify and close. |
| — (no thread; carried from 3787898986 via [plan 11](11-api-auth-records-sync.md) §1) | **Fix.** The verification code is the one peppered digest whose input has real entropy pressure: ~19.8 bits, looked up by code alone across every outstanding code, consumed by whoever types it, with no attempt cap. HMAC, a wider numeric code, and a failure cap; the API's `int` response is preserved. |

## Delivery

1. `accounts: fold creator names in one place` — subplan 1, with the migration, golden corpus, and crash regression.
2. `accounts: reconcile Java renames in one operation` — subplan 2.
3. `accounts: refresh linked Java identities on demand` — subplan 3.
4. `accounts: name errors by provider and subject` — subplan 4.
5. `accounts: batch identity loads` — subplan 5.
6. `accounts: harden the verification code` — subplan 6, landed 2026-08-17 in `8498da21`.

Replying on GitHub and resolving threads requires separate explicit authorization, per the
[directory README](README.md).

## Implementation notes for subplan 6

- **The cap is keyed on `(provider, subject)`, not on an account.** A redemption is the first thing
  many callers ever do, so the guesser may have no account yet, and creating one in order to
  rate-limit somebody defeats the point. This also made the guard reusable by
  [plan 01](01-consent-verification-ux.md) §1, whose code reservation is deliberately anonymous.
- **Holding a *correct* code is never charged as a failure.** The conflict branches prove the caller
  had a valid code; charging them would let anyone lock out an already-linked user by replaying that
  user's own successful code, turning the abuse control into the abuse.
- **The counter increments in one upsert.** A read-modify-write would let parallel attempts each read
  the same count and overwrite one another, so the cap could be evaded by not waiting for a reply.
- **`generate_verification_code` moved out of the bootstrap lambda** into the application layer, so a
  test can bind to the real factory rather than a copy of it. `/verify` still returns an `int`.
- **An integration test caught a real bug**: inside `case()` the `locked_until` bind loses its
  `InstantUTC` adapter and reaches asyncpg as a bare `Instant` it cannot encode.
- `docs/credential-hashing.md` now records the applied answer rather than pointing here for it.
