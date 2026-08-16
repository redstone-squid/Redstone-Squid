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

### Already addressed after `5edfd3e`

- The polymorphic row upgrade is superseded by `account_identities` plus stable `accounts.public_creator_id`.
- `permission_grants` and `permission_role_assignments` already key on `subject_account_id`.
- `PermissionService` is 262 lines of cohesive methods over `PermissionStore` and `SubjectRuleCache`.

## Subplans

1. **One definition of creator-name normalization**
   - Add `normalized_creator_name(value)` to `squid/accounts/infrastructure/models.py`, returning
     `func.lower(func.btrim(value))`, and define the generated column from that same function so one expression
     serves both. Confirm the emitted DDL still matches `lower(btrim(name))` so no migration is needed; if it does
     not, keep the literal string and add an architecture test asserting the two agree.
   - Delete `normalize_ign` from the domain and its re-export in `squid/accounts/domain/__init__.py`.
   - Convert every comparison to `CreatorAlias.normalized_name == normalized_creator_name(name)`:
     `get_alias_by_name`, `claim_unclaimed_alias`, `request_claim`, and the alias update inside
     `consume_code_and_link_account`.
   - In `_get_or_create_aliases`, use the expression for the lookup and the fallback re-select, and dedup by
     resolved alias id rather than by a Python-normalized string. This removes the `NoResultFound` crash path.
   - In `SuggestionRepository.creators`, replace `casefold()` with a `LIKE` against the expression, escaping `%`,
     `_`, and `\` in the Python query first — LIKE syntax is a separate concern from normalization, and the current
     code does not escape them either.
   - *Risk with a stated fallback:* `creator_aliases_normalized_name_prefix_idx` (`text_pattern_ops`) needs a prefix
     the planner can fold. `lower(btrim($1)) || '%'` is immutable over a parameter and should fold under a custom
     plan, but this must be confirmed by `EXPLAIN`. If the index drops out of the plan, keep Python normalization on
     this path only and document why: a wrong autocomplete prefix costs a missed suggestion, never a wrong credit.

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

## Interfaces and Tests

### Normalization

```python
def normalized_creator_name(value: ColumnElement[str] | str) -> ColumnElement[str]:
    """The SQL Postgres uses for `creator_aliases.normalized_name`.

    Comparisons must go through this rather than normalizing in Python: `str.lower()` and
    Postgres `lower()` disagree (`ΣΣ` → `σς` vs `σσ`, `İ` → `i̇` vs `i`), and `str.strip()`
    removes Unicode whitespace `btrim()` keeps. Computing the value on both sides made an
    alias unreachable and crashed a repeat submission crediting the same name.
    """
    return func.lower(func.btrim(value))
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
```

### Tests

- **Normalization equivalence** (`tests/integration/accounts/`, hypothesis): the value the generated column stores
  equals `SELECT lower(btrim(:x))` for generated text plus the curated set `ΣΣ`, `İ`, `Straße`, ` Foo `,
  `\tFoo\t`. This pins the invariant the Python implementation could not hold.
- **Crash regression**: a build crediting `ΣΣ` twice resolves to one alias id instead of raising `NoResultFound`.
  This test fails on current `HEAD`.
- **Round trip**: `get_alias_by_name("ΣΣ")` finds the alias a build crediting `ΣΣ` created.
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

### Before merging

Confirm the prefix index survives the expression rewrite:

```sql
EXPLAIN (ANALYZE, BUFFERS) SELECT name, account_id FROM creator_aliases
WHERE normalized_name LIKE lower(btrim($1)) || '%' ORDER BY normalized_name LIMIT 25;
```

Size the exposure against production, and confirm nothing already stored is unreachable:

```sql
SELECT count(*) AS total,
       count(*) FILTER (WHERE name ~ '[^\x20-\x7E]') AS non_ascii,
       count(*) FILTER (WHERE name <> btrim(name))   AS untrimmed
FROM creator_aliases;
```

No Alembic migration is expected; `alembic heads` should be unchanged.

## Disposition

| Thread | Disposition |
|---|---|
| 3765927329 — duplicating info | **Fix.** The duplication is not merely redundant: the two computations disagree and crash a repeat submission. One SQL expression now serves both the generated column and every comparison. |
| 3766109224 — handle name change | **Fix.** `refresh_java_identity` gives the rename an explicit atomic policy — refresh on link and on demand, retain old claimed aliases, create and claim an unheld new name, never transfer a held one. Background Mojang polling stays deferred. |
| 3766114978 — too many normalize functions | **Fix.** Resolved by deletion rather than by a utils module: `normalize_ign` and the `casefold()` variant both go, leaving one SQL definition. |
| 3766128577 — row upgrade | **Already fixed.** `account_identities` plus `accounts.public_creator_id` superseded it, and `squid/users` is gone from git. Verify and close. |
| 3766140695 — excessive mappings | **Retain, with cleanup.** Explicit mapping stays; SQLAlchemy cannot populate frozen domain dataclasses without coupling the domain to persistence. The actionable part — per-row queries — is fixed by `get_many` and by removing the `refresh` loop. |
| 3766202899 — outside Discord | **Fix.** Errors carry `(provider, subject)`, services gain `account_id`-keyed cores, and `me.py` stops rejecting non-Discord principals that hold an `account_id`. |
| 3775414045 — "what a service..." | **Retain, with rationale.** 262 lines of cohesive methods over `PermissionStore` and `SubjectRuleCache`; splitting it would scatter one authorization decision. |
| 3775418269 — should become a user id | **Already fixed.** `permission_grants` and `permission_role_assignments` key on `subject_account_id`. Verify and close. |

## Delivery

1. `accounts: compare creator names in SQL` — subplan 1, with the `ΣΣ` regression test.
2. `accounts: reconcile Java renames in one operation` — subplan 2.
3. `accounts: refresh linked Java identities on demand` — subplan 3.
4. `accounts: name errors by provider and subject` — subplan 4.
5. `accounts: batch identity loads` — subplan 5.

Replying on GitHub and resolving threads requires separate explicit authorization, per the
[directory README](README.md).
