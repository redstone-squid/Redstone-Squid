# De-privileging Discord in the account system

> **Status.** Done. Every milestone landed; the amendments building it forced are recorded
> inline below, each marked **Amended in build**.
>
> Discord is now one `IdentityProvider` among several, reached through an OAuth adapter.
> Every account-scoped write is keyed on `account_id`, and `Caller.discord_id` no longer
> exists. Adding a real second provider costs an enum line, a `for_provider` arm, an
> adapter class, three flat config fields, one `PROVIDER_FACTORIES` entry, and one
> `OAuthConfig._GROUPS` entry.

> **Amended for [PR #183 review plan 11](pr-183-review/11-api-auth-records-sync.md), which landed
> between this document being written and being started.** Nothing here changed in substance; the
> ground under it moved in three ways.
>
> - `Principal` is now `Caller`, `principal_allows` is `caller_allows`, and every `principal:`
>   parameter is `caller:`. `Caller.discord_id` is still the field M10 deletes.
> - **M3 and M9 shrink.** Plan 11 moved build edit authority out of the route into
>   `BuildService.apply_edit`, which takes a `BuildEditor(subject, discord_id)`. So the second
>   `assert principal.discord_id is not None` M3 planned to delete is already gone, and the
>   ownership comparison M9 rewrites now lives in the service rather than at `builds.py:126` —
>   which is where M9 wanted it anyway, and it arrives already reusable by the bot's two edit
>   paths. `_require_consented_user` still rejects a Discord-less caller, so the milestone's
>   substance is untouched.
> - "provider-neutral" is no longer used as a term of art anywhere in `squid/`; the prose here says
>   what it meant instead. The two migration filenames keep the word.
>
> Line references throughout were re-checked against `97af8d81`.

## Context

The account *model* already treats every identity provider alike. Migration `d6f7a8b9c0d1`
(`2026_08_11_1200-..._cut_over_provider_neutral_accounts.py`) renamed `users` → `accounts`,
introduced `account_identities(account_id, provider, subject, …)` with three symmetric providers,
and left `accounts` with no provider column at all. Four auth mechanisms — API keys, CLI device
codes, Paper/Fabric player grants, installation credentials — are already keyed on `account_id`
and mention Discord nowhere.

The baggage is entirely in the layers *around* that model, and it has a consistent shape: a
Discord snowflake sitting next to a perfectly good `account_id`, either as a denormalized column
or as the parameter a method is keyed on. Four things are broken today as a direct result.

- **A browser session cannot exist without a Discord ID.** `web_sessions.discord_id` is
  `NOT NULL`, `WebSessionIdentity.discord_id` is non-optional, and
  `WebSessionRepository.create_session` requires one. A second web login is unimplementable
  without touching the schema.
- **CLI and Minecraft callers cannot submit, edit, or vote**, despite holding a valid
  `account_id`. `squid/api/v1/builds.py:219` and `squid/api/v1/votes.py:59` both reject on
  `caller.kind != "account" or caller.discord_id is None`, and `builds.py:68` then
  `assert caller.discord_id is not None`.
- **Redeeming a Minecraft verification code mints a Discord identity as a side effect**
  (`squid/accounts/infrastructure/repository.py:466-472`), so a Minecraft-only user cannot link at
  all — and `unlink_java_identity(discord_id)` looks the account up *by Discord identity* to remove
  a *Java* identity, so a Discord-less account can never unlink either.
- **A proposer's Discord snowflake is published in the API.** `tags` stable keys are
  `f"user_{discord_id}_{hex}"` (`squid/tags/application/services.py:71`) and are returned verbatim
  as `BuildTag.key` (`squid/api/v1/schemas/builds.py:191`).

The goal is that Discord becomes one `IdentityProvider` among several, reached through an OAuth
adapter; every account-scoped write is keyed on `account_id`; and `Caller.discord_id` ceases to
exist. Adding a real second provider afterwards costs an enum line, a `for_provider` arm, an
adapter class, three flat config fields, and one four-line migration.

### Settled decisions

- Scope covers the core identity/session layer **and** the downstream denormalized columns in
  voting, notifications, tags, schematics, and builds' submitter.
- **Seams only** for a second provider, proven with a test-only fake adapter. No real OAuth app,
  no new secrets, no new UI.
- `IdentityProvider` stays a closed `StrEnum`; the per-provider subject *format* check moves out of
  SQL into the domain.
- No meaningful production data, so migrations are plain ALTERs with minimal downgrades.

### Non-goals

These are genuinely Discord and must not be touched. Listed explicitly so a later reader does not
"finish the job" by breaking things that are correct:

- `squid/messages/`, `squid/posts/`, `squid/starboard/`, and `squid/settings/`
  (`server_settings.server_id` is a real FK target for `guild_vote_emojis` and others).
- The `SubjectKind.DISCORD_ROLE` half of `squid/permissions/` — a grant attached to a Discord role
  is legitimately Discord-shaped.
- `VoteActor.discord_id` — a real guild-membership fact consumed by
  `squid/voting/application/policies.py:34` for role weighting.
- `PendingNotificationDelivery.discord_id` — a DM job legitimately carries a Discord address.

The rule is *Discord things stay Discord; denormalized copies of Discord things do not.* Two
existing invariants must also survive: `subject_for`'s hardcoded `guild_id=None`
(`squid/api/security.py:100-107`), so a guild-scoped grant can never authorize an HTTP call, and
`AccountIdCache`'s never-create rule (`squid/bot/utils/permissions.py:48-116`), so a permission
check never mints an account row.

## Design decisions

### D1. The OAuth adapter

One module `squid/auth/application/providers.py` (~150 lines), not a package — there is one real
adapter and one test fake, and a package would be three files holding what fits on a screen.

```python
# squid/auth/domain/oauth.py (new)
@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """A verified subject returned by one authorization-code exchange."""
    provider: IdentityProvider
    subject: str
    display_name: str | None = None


# squid/auth/application/providers.py (new)
class OAuthProvider(Protocol):
    """One external authorization-code identity source."""

    slug: str
    """The URL segment this provider is reached at, e.g. "discord"."""
    provider: IdentityProvider
    """The identity namespace a successful exchange lands in."""

    def authorize_url(self, *, state: str, code_challenge: str) -> str: ...
    async def fetch_identity(self, *, code: str, code_verifier: str) -> ExternalIdentity: ...
```

`fetch_identity` deliberately swallows the token exchange rather than exposing a separate
`exchange` step. The access token has exactly one use — reading `/users/@me` — and no caller
outside the adapter should ever hold it; splitting the two forces the token through
`WebSessionService`, which then has to decide whether to log, store, or drop it, and the answer is
always "drop it". An OIDC provider whose token response already carries an `id_token` makes zero
further requests under this signature. The cost is that "also fetch the user's email" becomes an
adapter change rather than a service change, which is correct, since scopes are per-provider anyway.

`scope` is not a Protocol member: it is adapter-private (`"identify"` for Discord), baked into
`authorize_url`. Hoisting it would imply providers share a scope vocabulary, which they do not.

**Config stays flat per provider**, because `_ProcessSettings` sets `env_nested_max_split=1`
(`squid/config.py:870`). `SQUID_OAUTH_DISCORD_CLIENT_ID` cannot split into
`oauth → discord → client_id`, so a `dict[str, OAuthProviderConfig]` shape is unreachable from the
environment. Keep the existing field names and add a derived accessor:

```python
@dataclass(frozen=True, slots=True)
class OAuthClientCredentials:
    """One provider's complete authorization-code client registration."""
    client_id: str
    client_secret: SecretStr
    redirect_uri: AnyHttpUrl


class OAuthConfig(_FrozenModel):
    """Authorization-code client registrations, one flat group per provider."""

    discord_client_id: str | None = None
    discord_client_secret: SecretStr | None = None
    redirect_uri: AnyHttpUrl | None = None
    """Discord's callback. Named without a provider prefix for compatibility with
    SQUID_OAUTH_REDIRECT_URI, which is registered in the Discord developer portal."""
    session_ttl_hours: int = Field(default=336, ge=1)

    def clients(self) -> Mapping[IdentityProvider, OAuthClientCredentials]:
        """Every provider whose credentials are completely configured."""
```

`_require_complete_credentials` stays, generalized to loop over the groups. Env var names do not
change. A second real provider is three more flat fields plus one registry entry.

`UpstreamHttpConfig` (`squid/config.py:491-517`) is **untouched** — it describes *Discord's*
upstream, and its loopback escape is what `tests/unit/auth/application/test_web.py:107` and
`tests/fuzz/api/fake_upstreams.py` depend on. The test fake bypasses config entirely, constructed
directly and injected; that is the point of passing `Mapping[str, OAuthProvider]` into the service
rather than having it read config.

**The fake claims `IdentityProvider.BEDROCK`, not a new `TEST` member.** A test-only enum member
would pollute the production `match` exhaustiveness that D4 depends on, and reusing `DISCORD` would
prove only the slug routing, not that a second namespace lands correctly in `account_identities`.
Bedrock is a provider this project would plausibly add for real and already has subject validation.
Name it `FakeXboxOAuthProvider`, slug `"bedrock"`, in `tests/unit/auth/application/fakes.py`.

### D2. Route shape: `/v1/auth/{provider}`, no aliases

`/v1/auth/discord` *is* an instance of the template, so `GET /v1/auth/discord` and
`GET /v1/auth/discord/callback` keep working byte-identically: `SQUID_OAUTH_REDIRECT_URI` needs no
coordinated change in the Discord developer portal, and the frontend's hardcoded URLs keep working.
An alternative like `/v1/auth/login/{provider}` would make `/v1/auth/discord` a permanent orphan
alias.

Two consequences:

- **Route-ordering hazard.** `GET /v1/auth/{provider}` would swallow `GET /v1/auth/csrf`. FastAPI
  matches in declaration order, so `csrf_token` must stay declared above the templated route, with
  a comment saying why. This failure is silent, so the regression test is not optional.
  (`/v1/auth/logout` is POST and does not collide, but move it above too for symmetry.)
- **Contract churn.** The path becomes templated and gains a `provider` path parameter. Operation
  ids `browser_authorization_start` / `browser_authorization_callback` are preserved, so
  `squid/api/openapi.py:56-57,301` needs only its path strings updated.

Validate the segment with `Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]` and return
`NotFoundError` for an unknown or unconfigured slug — "this deployment has no GitHub login" is a
404 fact about the resource, not a credential failure.

**`oauth_states` gains a `provider` column and `callback` refuses a state whose provider does not
match the slug in the URL.** This is a security fix, not bookkeeping: without it, a state minted
for provider A is redeemable at provider B's callback, which is the IdP mix-up class. It is the
reason the column is required and the reason it lands in the same commit as the templated route.

### D3. The neutral atomic get-or-create

`get_or_create_identity(provider, subject)` replaces `get_or_create_discord`, reusing the existing
race-safe transaction shape verbatim (`squid/accounts/infrastructure/repository.py:170-198`: insert
a speculative account, `INSERT … ON CONFLICT (provider, subject) DO NOTHING RETURNING id`, and on a
lost race delete the speculative row and re-read the winner). Only the hardcoded provider changes.
Do not invent a new transaction shape.

`consume_code_and_link_account` rekeys onto `account_id`, and the account-creation policy moves to
the call site in `squid/bot/verify.py`, which genuinely holds a Discord identity and has always
intended to create one. The current code is wrong on three counts: a Minecraft-only or CLI-only
caller cannot link at all; a code-redemption path writes a row in a namespace it has no evidence
for; and the "create an account" policy is buried in a repository where no reviewer of the linking
flow will see it.

Discord convenience moves to the Discord transport as
`squid/bot/utils/accounts.py::account_id_for(accounts, user)`, which also deletes the
`assert account.id is not None` repeated at eight bot call sites.

| now | becomes |
|---|---|
| `get_account(discord_id)` | `get_account_by_identity(provider, subject)` |
| `get_or_create_account(discord_id)` | `get_or_create_identity(provider, subject)` |
| `grant_current_consent(discord_id)` | deleted; `grant_current_consent_for_account` takes the name |
| `link_minecraft_account(discord_id, …)` | `link_minecraft_account(account_id, …)` |
| `unlink_minecraft_account(discord_id)` | `unlink_minecraft_account(account_id)` |
| `request_alias_claim(discord_id, name)` | `request_alias_claim(account_id, name)` |

### D4. `IdentityProvider`: keep the enum, keep `provider IN (…)`, drop the subject regex

Format validation moves into a new exhaustive domain constructor:

```python
@classmethod
def for_provider(
    cls, provider: IdentityProvider, subject: str, *,
    display_name: str | None = None, verified_at: Instant | None = None,
) -> "AccountIdentity":
    """Build an identity in a provider's canonical subject form.

    The `match` is exhaustive by construction, so adding a member to `IdentityProvider`
    is a type error here until its subject format is stated. That exhaustiveness is
    the reason the enum stays closed.
    """
```

Arms: `DISCORD` → `^[1-9][0-9]*$` and `< 2**63`; `BEDROCK` → same digits, `0 < xuid < 2**64`;
`JAVA` → normalize through `str(UUID(subject))`, which also lowercases and hyphenates. The three
existing classmethods stay as typed conveniences and delegate here, so no construction site changes.

*Membership* validation stays in SQL but is generated from the enum so it cannot drift, which is
already this codebase's idiom (`_KIND_VALUES` in `squid/voting/infrastructure/models.py:239-241`):

```python
_PROVIDER_VALUES = ", ".join(f"'{p.value}'" for p in IdentityProvider)
CheckConstraint(f"provider IN ({_PROVIDER_VALUES})", name="account_identities_provider_check")
```

Rejected: a **lookup table** buys referential integrity nobody needs and turns "add a provider"
into a seed migration plus a join on every identity read. **Dropping the check** loses write-time
safety — a typo'd provider string from raw SQL becomes a `ValueError` deep inside `_to_identity` on
some unrelated read path instead of an error at the write.

### D5. `staff_discord_ids` becomes a permission node

Two questions hide behind one config key.

*"May this caller read staff inbox items?"* is per-caller, called only from
`squid/api/v1/notifications.py:143,166`. Delete `can_view_staff` from the service and repository and
compute it in the route with machinery already imported there:
`await caller_allows(permissions, caller, BUILD_SUBMISSION_VIEW_PENDING)`. Staff
notifications are *about* pending submissions — exactly what `_staff_role_ids`'s docstring already
names as the intended refinement. This adds no service dependency, names no provider, and is
credential-bounded, so a leaked API key without the node cannot read staff items. That last
property is new and strictly better than today.

*"Whom do we notify?"* is a set query over all accounts that cannot run the resolver per row. It
keeps its existing `global-admin` role subquery, minus the `staff_discord_ids` half.

**The catch, which constrains the fix:** the bot owner is in the audience today only because
`Subject.is_bot_owner` short-circuits in code (`squid/permissions/domain/resolution.py:312`) and is
derived from `bot.is_owner(user)` — it is never a database row, so a set query cannot see it. That,
not Discord-keying, is the actual reason the allowlist exists. Handle it with a guarded, idempotent
role seed in the migration; it no-ops when no such account exists, and the owner can otherwise use
the existing `/role assign`.

### D6. Other calls

- **`builds.submitter_id`**: rekey outright, own milestone, **no migration**.
  `builds.submitter_account_id` is already a `NOT NULL` FK, `BuildService.submit_for_account` and
  the `submitter_account_id` filter already exist, and `submitter` is absent from `BuildDetail`,
  `BuildSummary`, and `contracts/openapi.json` — so the API contract does not move.
  `Build.submitter_id` survives as read-only derived state for bot rendering
  (`squid/bot/submission/search.py:204`); rename it to `submitter_discord_id` so it stops sitting
  ambiguously beside `submitter_account_id`. That ambiguity is precisely what let the edit
  ownership test compare a snowflake to a snowflake while a perfectly good account id sat one
  attribute away — a test that now lives in `BuildService.apply_edit` rather than in the route.
- **Tags stable key** → `f"user_{uuid4().hex}"`. It is never parsed — the only literal comparison
  (`squid/records/infrastructure/repository.py:781,814`) is against an official key — so the format
  is free. Rewrite existing rows in the migration; leaving them leaves snowflakes published.
- **`Caller.discord_id`**: delete it. After the milestones below every reader is gone, and since
  `subject_for` hardcodes `guild_id=None`, an HTTP caller can never act on a Discord fact anyway —
  so a snowflake on the caller is an identifier with no legitimate HTTP use. Keeping it "as an
  optional convenience" is exactly the affordance that produced an `assert caller.discord_id is
  not None` on a submission path that has an `account_id` in hand. `UserMe.discord_id` in the *response* stays: it is read off the account's
  identities (`squid/api/v1/schemas/me.py:28`), which is the correct pattern.

## Milestones

Each is one commit unless noted.

### M0 — `alembic: linearize the two heads that fork at b1c2d3e4f5a7`

> **Amended in build: already done, the other way.** By the time this started, revision
> `c6d7e8f9a0b1` had landed with `down_revision = ("b2c3d4e5f6a8", "e5f6a7b8c9d2")` — the
> merge node this section argued against, chosen by whoever hit the fork first. `alembic
> heads` prints one head, which is all the milestone was a prerequisite for, so nothing
> was re-pointed. The tradeoff stands as written for the next fork.

**Prerequisite; nothing else can add a migration.** `b2c3d4e5f6a8` (nullable vote thresholds) and
`e5f6a7b8c9d2` (fold creator names) both point at `b1c2d3e4f5a7`, nothing points at either, and
`tests/integration/test_alembic_migrations.py` upgrades to `"head"`, which raises on multiple heads.

Re-point `e5f6a7b8c9d2`'s `down_revision` to `"b2c3d4e5f6a8"` rather than adding a merge revision:
the two are independent (disjoint tables — vote sessions vs. creator aliases), `e5f6a7b8c9d2` is
chronologically later by filename, and a merge node is a permanent empty revision every future
reader must decode. Note in the commit body that any developer database already at `e5f6a7b8c9d2`
must `alembic downgrade b1c2d3e4f5a7` before pulling; if that is unacceptable, `alembic merge`
gives the same outcome with the extra node.

### M1 — `accounts: validate identity subjects in the domain`

Pure refactor, no behaviour change. Add `AccountIdentity.for_provider` (D4); drop
`account_identities_subject_format_check`; generate `account_identities_provider_check` from the
enum in `squid/accounts/infrastructure/models.py:71-81` and comment that `for_provider` is the
format authority.

Tests — new `tests/unit/accounts/test_identity_subjects.py`: per-provider accept and reject, Java
UUID normalization (uppercase and unhyphenated input round-trip to canonical), and a loop over
`IdentityProvider` asserting every member has an arm, so a new member without one fails at runtime
as well as under the type checker.

### M2 — `auth: route browser login through a provider adapter` (three commits)

> **Amended in build: M2a must land after M3, or browser writes break.** Deleting
> `WebSessionIdentity.discord_id` leaves `Caller.discord_id` `None` for every browser
> session, and until M3 rekeys them the write gates still read
> `caller.discord_id is None` — so a logged-in browser user would silently lose the
> ability to submit, edit, and vote for as long as the two milestones were apart. The
> sequencing graph below does not capture this. Built in the order
> M1 → M4 → M5 → M7 → M8 → M9 → M3 → M6 → M2 → M10 → M11.
>
> **Amended in build: M2a is contract-neutral, and its route keeps the literal paths.**
> The adapter and the templated path are separable if M2a's routes call
> `authorize_url("discord", …)` against the unchanged `/v1/auth/discord`, which is what
> was done — so `contracts/openapi.json` moves in M2b alone, exactly once.

**M2a — extract the adapter.** New `squid/auth/domain/oauth.py` and
`squid/auth/application/providers.py`. `OAuthState` gains `provider`; `WebSessionIdentity.discord_id`
deleted. **Move `WebSessionRepository` out of the service module** (`web.py:33-52`) into
`squid/auth/application/ports.py` — a Protocol declared in a service module is misplaced — dropping
`discord_id` from `create_session`. `DiscordOAuthService` → `WebSessionService` taking
`providers: Mapping[str, OAuthProvider]`, with `authorize_url(slug, redirect_to)` and
`callback(slug, code, state, *, user_agent)`; `callback` rejects a provider mismatch and calls
`get_or_create_identity`. `aclose` is removed — the httpx client becomes bootstrap-owned and pushed
onto `self.resources`. `hash_web_session_token` and `consent_pending` are untouched. Wire adapters
in `squid/bootstrap.py:562-574` from a `dict[IdentityProvider, factory]` registry.

Migration: `web_sessions` DROP `discord_id`; `oauth_states` ADD
`provider text NOT NULL DEFAULT 'discord'` then DROP DEFAULT. Downgrade re-adds `discord_id`
nullable and says in its docstring why backfilling it is pointless.

Tests: new `tests/unit/auth/application/fakes.py` holding `FakeXboxOAuthProvider` and the
`SessionRepository` fake moved out of `test_web.py`. Keep the two existing Discord cases (PKCE
durability and hashed session, loopback upstream honoured). **Add** a full login through the fake
landing a `BEDROCK` identity — the seam proof — plus a cross-provider state replay raising
`AuthenticationError` and an unknown slug. Update `tests/fuzz/api/database.py:316-326,373`, which
seeds `web_sessions.discord_id` directly and is outside the default `testpaths` sweep, so it will
rot silently otherwise.

**M2b — templated route.** Route signatures in `squid/api/v1/auth.py`, `csrf_token` declared first
with a comment. Update path strings in `squid/api/openapi.py:56-57` and regenerate
`contracts/openapi.json` and `web/src/generated/{sdk,types}.gen.ts` **in the same commit**, or
`tests/unit/api/test_openapi_contract.py` fails. New `tests/unit/api/test_auth_routes.py`,
including the `/v1/auth/csrf`-not-swallowed regression. `tests/unit/api/fakes.py::build_app`
already accepts `web_auth=`, so no fixture change is needed.

**M2c — frontend.** Collapse the three duplicated sign-in URL builders in
`web/src/lib/submission-api.ts` (~338, 372, 413) into one
`signInUrl(config, returnTo, provider = "discord")`. No component change and no Astro change —
`web/src/pages/{cli,minecraft}/link.astro` contain no auth references; they render components that
already call `api.signInUrl(...)`.

### M4 — `accounts: key the account service on identities, not Discord`

*Sequenced before M3 and M5–M9.* No migration.

Port: `get_or_create_identity(provider, subject)`; delete `get_by_discord_id` (a pure alias for
`get_by_identity`) and `get_or_create_discord`; `unlink_java_identity(account_id)`;
`consume_code_and_link_account(*, account_id, code, consent)`. Service methods per the D3 table.
Repository: rekey `:166-198`, `:211-225`, and `:421-505`, deleting the identity mint at `:466-472`
and renaming the `discord_account` local. Everything downstream of it — `existing_java`, the
`java_holder` conflict handling, `_reconcile_java_name` — is unchanged.

New `squid/bot/utils/accounts.py::account_id_for`; update the eight bot call sites (`verify.py`,
`notifications.py`, `permissions.py:71-74`, `voting/{vote,publisher,sessions}.py`).
`squid/bot/utils/permissions.py:70` switches to `get_account_by_identity`, and its never-create
invariant gets reaffirmed in the docstring.

Tests: rewrite `FakeAccountRepository` in `tests/unit/accounts/application/test_services.py` onto
the new port. **Add the two cases that prove the point** — an account with no Discord identity can
link *and* unlink Minecraft, both impossible today. Integration: `get_or_create_identity` is
idempotent under a concurrent race for `BEDROCK`; redeeming a code for a missing account raises
`AccountNotFoundError` rather than creating one; redeeming a code creates no Discord identity.
Confirm query counts are unchanged in `test_account_query_counts.py`.

### M5 — `voting: resolve vote actors by account`

Migration drops `votes.discord_id`. `VoteSelection.discord_id` deleted; **`VoteActor.discord_id`
stays** (non-goal). Ports, services, and the repository upsert drop the argument.
`DiscordRestActorResolver` gains an injected account-id → snowflake lookup, modelled on
`squid/minecraft_auth/infrastructure/accounts.py::PostgresAccountIdentityAuthorizer`; an account
with no Discord identity resolves to `None`, which existing code already treats as "not a member" —
correct, since a non-Discord account has no guild role weight.

Watch the refresh loop (`squid/voting/application/services.py:344-365`): the existing
`(guild_id, discord_id)` cache does not cover the new lookup, so it adds a query per uncached
actor. Measure before batching, and note it in the commit rather than pre-optimizing.

### M3 — `api: key write authorization on the account, not on Discord`

*Sequenced after M5* so `votes.py` is rewritten once, not twice.

`_require_consented_user` (`builds.py:219`) and the vote gate (`votes.py:59`) drop **both** the
`kind` and the `discord_id` tests, keying on `account_id is None`. Dropping only `discord_id`
achieves nothing observable, since `kind != "account"` rejects CLI and Minecraft callers one
line earlier. Delete the surviving `assert caller.discord_id is not None` (`builds.py:68`); the
edit path's twin went when `BuildService.apply_edit` took over ownership, and `BuildEditor`
already accepts `discord_id=None`. Use the
`ConsentRequiredError(account_id=…)` keyword form across `minecraft_auth.py`, `submissions.py`, and
`cli_auth.py`.

`squid/accounts/errors.py`: `_identity_context` loses its `discord_id` branch and becomes
`(account_id, provider, subject)`; `ConsentRequiredError` drops the positional `discord_id`;
`AccountAlreadyLinkedError` drops it too, and its user-facing string is reworded off "This Discord
account…" since it is raised for any provider conflict.

Tests: `tests/unit/api/test_build_writes.py` and `test_vote_writes.py` gain a CLI caller
(`kind="cli"`, `account_id` set, no `discord_id`) that can submit and vote; keep the anonymous
rejection cases. `tests/unit/accounts/test_errors.py` gains a Java-provider conflict asserting
`provider="java"` rather than an implied Discord.

### M6 — `notifications: gate staff items on a permission node`

Migration drops `notification_deliveries.discord_id` and seeds the owner's `global-admin` role
assignment (guarded, `ON CONFLICT DO NOTHING`). `can_view_staff` deleted from the port, service,
and repository; `_staff_discord_ids` and its constructor parameter deleted; `claim_deliveries`
joins `account_identities` to fill the DM address at claim time and skips deliveries whose account
no longer has a Discord identity. The write path already did this join
(`squid/notifications/infrastructure/repository.py:636-678`), so the cost merely moves. Delete
`NotificationConfig.staff_discord_ids` (`squid/config.py:802`), the `squid/bootstrap.py:309`
argument, and the `tests/unit/test_config.py:177` assertion.

**Two user-visible behaviour changes for the commit body:** unlinking Discord now suppresses
pending DMs (the correct reading of an unlink), and the bot owner needs a real role row to receive
staff notifications.

### M7 — `tags: attribute definitions to accounts`

Migration on `tag_definitions` and `build_tag_assignments`: add `created_by_account_id` FK
(`ON DELETE SET NULL`), translate from `account_identities`, drop `created_by_discord_id`, and
rewrite `stable_key ~ '^user_[0-9]+_'` rows to
`'user_' || replace(gen_random_uuid()::text, '-', '')`. Document in the migration docstring that
this invalidates bookmarked `?tag=user_…` queries — the intended cost.

`assign_showcase`'s ownership check becomes `Build.submitter_account_id == actor_account_id`,
dropping an `AccountIdentity` join. New `tests/integration/tags/`: an account with no Discord
identity can assign a showcase tag to its own build and cannot to someone else's.

### M8 — `schematics: attribute uploads to accounts`

Smallest milestone, about eight call sites. Same add/translate/drop, landing
`uploaded_by_account_id` beside the existing `rights_attested_by_account_id` FK
(`squid/schematics/infrastructure/models.py:184-191`) so the table carries one attribution style
rather than two.

### M9 — `builds: own submissions by account`

> **Amended in build: M7 must land before M9.** `_setup_tag_assignments` writes
> `created_by_discord_id=build.submitter_id`. Once M9 makes `submitter_id` derived
> read-only state, that is `None` for a build being created, so every tag assignment on a
> new build would lose its attribution until M7 lands `created_by_account_id`. Doing M7
> first lets M9 write `created_by_account_id=build.submitter_account_id` directly.

No migration. `BuildService.submit(*, submitter_account_id, …)`; `DoorSubmissionInput` field
renamed; the ownership test inside `BuildService.apply_edit` becomes
`submitter_account_id == actor.subject.account_id`, and `BuildEditor.discord_id` goes with it;
`_resolve_submitter_account_id` collapses to an existence check and `_get_or_create_account`
(`squid/builds/infrastructure/repository.py:457-472`) is **deleted** — the last snowflake→account
minting path outside the accounts context. `_page_filter`/`list_page`/`count` lose the
`submitter_id` parameter and its `AccountIdentity` join, which also removes the `func.distinct` the
join forced on the count query. `BuildDraft.submitter_id` and its `to_build` entry go.

Trailing commit: rename `Build.submitter_id` → `submitter_discord_id` (6 sites).

Strongest available test: persisting a build no longer creates an account as a side effect.

### M10 — `api: drop the Discord snowflake from the HTTP caller`

Delete `Caller.discord_id` (`squid/api/security.py:38`) and its assignment (`:136`);
`grep -rn "caller.discord_id" squid/` must return nothing. Rewrite
`test_a_discord_caller_still_reports_its_discord_id` (`tests/unit/api/test_me_routes.py:67`) to
build `UserMe` from the account's identities rather than from the caller — it currently passes for
the right reason through the wrong field. Add a
ratchet in `tests/architecture/test_boundaries.py`: no module under `squid/api/` names `discord_id`
except `squid/api/v1/schemas/me.py`.

### M11 — `docs: record that Discord is one provider among several`

Update this document's status block. Comment `.env.example`'s OAuth group with the
flat-per-provider convention and why (`env_nested_max_split=1`).

## Sequencing

```
M0 ──▶ M1 ──▶ M2a ──▶ M2b ──▶ M2c
        │
        └────▶ M4 ──┬──▶ M5 ──▶ M3 ──┐
                    ├──▶ M6 ─────────┤
                    ├──▶ M7          ├──▶ M10 ──▶ M11
                    ├──▶ M8          │
                    └──▶ M9 ─────────┘
```

M0 blocks every migration. M1 blocks M4, which uses `for_provider`. M4 blocks M5–M9, which all need
`account_id_for`. M5 before M3. M2 is independent of M4 apart from `get_or_create_identity`, so it
can proceed in parallel once M4 lands.

### Risks, ranked

1. **M2b contract churn.** `contracts/openapi.json` and `web/src/generated/*` must be regenerated
   in the same commit or `tests/unit/api/test_openapi_contract.py` fails. The FastAPI
   route-ordering hazard is silent if you get it wrong.
2. **M6's behaviour changes** — DM suppression on unlink, and the owner needing a real role row.
   Both are correct; both are user-visible.
3. **M0's re-point** breaks any developer database sitting at `e5f6a7b8c9d2`. One line in the
   commit body, one message to the team.
4. **M5's per-selection snowflake lookup**, which the existing `(guild_id, discord_id)` cache does
   not cover.
5. **`tests/fuzz/api/`** is outside the default `testpaths` sweep and seeds `web_sessions.discord_id`
   directly, so it rots silently unless M2a updates it.

## Verification

Per `CLAUDE.md`, run the smallest covering set during development with `--no-cov`, and defer the
full suite to CI.

**Per milestone**

- `uv run pytest tests/unit/<context> --no-cov` for the touched context.
- Any milestone with a migration: `uv run alembic heads` must print exactly one, and
  `uv run pytest tests/integration/test_alembic_migrations.py --no-cov`.
- `git diff --check`; formatting and linting over changed files, then `just typecheck` (pyrefly,
  which is project-wide -- read only the errors in files this milestone touched).

**The four assertions that prove the goal was met.** Each is a test that fails on `master` today:

1. A full browser login through `FakeXboxOAuthProvider` lands a `BEDROCK` identity and issues a
   session (M2a) — proves nothing on the session path assumes Discord.
2. A CLI caller (`account_id` set, no `discord_id`) submits a build and casts a vote (M3).
3. An account with no Discord identity links *and* unlinks a Minecraft account (M4).
4. `grep -rn "caller.discord_id" squid/` returns nothing, enforced by the architecture test (M10).

> **Amended in build: what the build actually found.** Three defects surfaced that the
> plan did not anticipate, all of them pre-existing and none of them caused by this work.
> `assign_showcase` raised on every call against a real database, twice over — its
> `value_type` read back as `str` from a bare `Text` column while `_split_value` compared
> with `is`, and it bound `updated_at` as a stdlib datetime under an `InstantUTC`
> decorator that does that conversion itself. Both are fixed in M7, which is where the
> first integration test of that path lives. Separately, `web/src/generated/*` was already
> ~284 lines stale against the committed contract, so M2b's regeneration picks up that
> backlog as well as its own change.
>
> Three test failures predate this work and remain: two in
> `tests/integration/test_alembic_migrations.py` (column-comment drift across 13 columns,
> and a `principal`/`caller` column rename) and one threshold-constraint case in
> `tests/integration/voting/`. `tests/integration/fuzz/` needs BuildKit and does not run
> locally. All confirmed by stashing.

**End to end, after M9**

- `uv run pytest --no-cov` once, plus `tests/fuzz/` explicitly.
- Run the bot against a dev guild: `/account link`, `/account unlink`, `/account refresh`,
  `/perm grant`, and a vote — these exercise every rekeyed bot call site.
- Run the API and complete a real Discord browser login end to end, confirming `/v1/auth/discord`
  still works unchanged and `/v1/auth/csrf` is not swallowed by the templated route.
- Confirm the regenerated `contracts/openapi.json` differs only in the `/v1/auth/*` paths.
