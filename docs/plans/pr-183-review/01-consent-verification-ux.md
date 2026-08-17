# PR #183 Review: Consent and Verification UX

## Scope

Seven review comments on [PR #183](https://github.com/redstone-squid/Redstone-Squid/pull/183) at or before the
`5edfd3e` cutoff. Every anchor below is the comment's `original_line` at its `original_commit_id`, resolved with
`git show <commit>:<path>`, because five of the seven are one-word comments whose meaning is entirely in the line
they point at.

| Thread | Anchor | Comment | The line it points at |
|---|---|---|---|
| 3765772656 | `bot/consent.py:34` @`ae330b8` | "simplify" | `"Selecting **Agree and link** records the notice version and time of your consent. "` |
| 3765867741 | `bot/consent.py:34` @`c6dafc3` | "add a preview?" | `"Linking also claims any existing build credit under your verified Minecraft username, "` |
| 3765882882 | `bot/verify.py:115` @`c6dafc3` | "not user friendly" | `body = "\n".join(f"**#{claim.id}** {claim.alias_name} (user {claim.user_id})" …)` |
| 3765888733 | `bot/verify.py:130` @`c6dafc3` | "ux, who is claimant" | `t(locale, _("Credited **{name}** to the claimant."), name=claim.alias_name)` |
| 3765893624 | `bot/verify.py:138` @`c6dafc3` | "confusing for no reason" | `"""Close a creator credit claim without crediting the claimant."""` |
| 3765913970 | `bot/verify.py:142` @`c6dafc3` | "ux" | `t(locale, _("Rejected the claim for **{name}**."), name=claim.alias_name)` |
| 3766207128 | `users/errors.py:91` @`c6dafc3` | "we should add context about which account claimed it" | `AliasAlreadyClaimedError.__init__` |

The first two threads sit on the *same line number* in different commits, and reading them together is what tells
you what to build. "simplify" landed on the notice before it grew; "add a preview?" landed on the sentence that
grew it. The notice got longer in between, and the second comment is not a request for a third paragraph. **The
answer to both is the same: less prose, and concrete values instead.**

Thread 3765893624 is worth spelling out because it is easy to misread as a docstring nit: `reject_claim`'s docstring
*is* its user-visible slash-command description, because discord.py derives the description from the docstring.

`squid/users` no longer exists; thread 3766207128's file is now `squid/accounts/errors.py`.

Threads on `squid/users/*` that are about identity storage rather than its presentation belong to
[plan 02](02-user-identity-persistence.md); this plan owns only what a user or a staff reviewer reads. Plan 02 §6
(verification-code entropy, keyed digest and attempt caps) touches the same table as §1 here — see the ordering
dependency in that subplan.

## Findings

Audited at `9692dd7e`. Two of the three findings in this plan's first draft have moved, and one of its intended
changes turned out not to be implementable as written. Both are recorded here rather than quietly dropped, so this
plan is not read as reverting work that has since landed.

### The requested preview cannot be built without a schema change

This is the central finding. `/account link <code>` sends the consent prompt and waits on it
(`squid/bot/verify.py:45-52`) **before** the code is redeemed. The only code path in the system is
`consume_code_and_link_account` (`squid/accounts/infrastructure/repository.py:414`), which is a single transaction
that sets `valid = False` and writes the identity, the consent receipt and the alias reconciliation together. There
is no read-only lookup: nothing can learn a code's `minecraft_uuid`, its `username`, or the creator credit at stake
without spending the code.

So the current notice is prose because prose is all it can honestly say. Restructuring that prose into a prettier
card would satisfy "simplify" and leave "add a preview?" unanswered — the preview needs a code the bot can *hold*.

Two consequences of the same gap are already user-visible:

- **You agree before you find out the code was wrong.** `InvalidVerificationCodeError` is raised inside
  `link_minecraft_account` (`squid/accounts/application/services.py:100-118`), i.e. after the prompt is answered. A
  mistyped code costs a full read of the notice and a button press before it fails.
- **You cannot tell you are about to link the wrong account.** The code is the whole binding, so a code typed from
  the wrong window links whatever that code was issued for, with no name shown at any point.

A wrong code is *already* a free, non-consuming probe today: `repository.py:438-439` returns an empty
`VerificationLinkResult()` before any write. This matters for §1's security argument — a preview does not create an
oracle that did not exist; it changes what a *correct* guess reveals before it commits.

### Claimant presentation partly landed, and the seam for the rest was left deliberately

`_claimant` (`verify.py:206-219`) already resolves a claimant as Discord mention → Java IGN → `account {id}`, and
`pending_alias_claims(with_claimants=True)` already batches the load — three selects for a four-claim queue, pinned
by `tests/integration/accounts/infrastructure/test_account_query_counts.py:148-170`. Plan 02 §5 built that on
purpose; `_claimant`'s own docstring says "Plan 01 replaces this with a fuller presentation; the batched load is
here so that work does not have to reintroduce a query per claim to do it."

What is still ID-only is therefore narrower than the first draft claimed, but it is not empty:

- **The approve and reject confirmations name nobody at all** (`verify.py:188`, `:201`) — thread 3765888733 exactly.
  `get_claim` (`repository.py:336-346`) never loads a claimant, so naming one needs a load that does not exist yet.
- **The `alias_claims_pending` autocomplete still shows `f"account {claim.account_id}"`**
  (`squid/suggestions/infrastructure/providers/records.py:92-105`). Its `PendingAliasClaims` protocol
  (`records.py:80-83`) declares `pending_alias_claims()` with no `with_claimants`, so that surface *cannot* ask for
  the data it needs.
- **The queue row itself is untranslated** — `verify.py:165` is an f-string; only the empty-queue fallback goes
  through `t(...)`. Thread 3765882882 ("not user friendly") is on that line.

One constraint on any "better" presentation: a Discord identity never gets a `display_name`.
`get_or_create_identity` (`repository.py:179-184`) writes only `account_id`, `provider`, `subject` and
`verified_at`, and no path ever fills it in; only Java names are maintained (`repository.py:476-477`, `:524-525`).
The raw `<@id>` mention is therefore genuinely the best available rendering — Discord resolves it client-side — and
should be kept rather than "improved" into a stored name.

### `AliasAlreadyClaimedError` still says only that somebody else has the name

Unchanged since the review (`squid/accounts/errors.py:156-168`): both `context` and `public_context` carry only
`{"name": name}`. Raised at `repository.py:321` (`request_claim`) and `:400` (`resolve_claim` approving a held
alias). The two raise sites have different audiences and different next actions — a user who should ask staff to
look, and a staff reviewer who needs to be told the `reassign` flag exists — and today they produce identical text.

The information needed to fix this is already in hand at the first site: `get_alias_by_name` (`repository.py:260-270`)
outer-joins `accounts` precisely to pick up `public_creator_id`, and `CreatorAlias` carries it
(`squid/accounts/domain/models.py:215-229`). So naming the conflicting creator costs no extra query there.

### Link says less about a link than refresh says about a refresh

`link_minecraft_account` returns `CreatorAlias | None` and throws away `VerificationLinkResult.refresh`
(`squid/accounts/application/ports.py:24-36`), whose docstring states it deliberately carries "everything a rename
produced — the previous name, retained credits, a contested name and the claim it opened". Meanwhile `/account
refresh` renders five branches of exactly that value through `_refresh_message` (`verify.py:222-265`), tested in
`tests/unit/bot/test_verify_messages.py`.

The result is that linking is the *less* informative of the two paths over the same reconciliation. In particular
the contested branch — your verified name is credited to somebody else, and a staff claim was opened — is rendered
on refresh and invisible on link. This is the concrete content of "one consistent state transition vocabulary": not
a copy-editing pass, but one renderer.

### Smaller UX defects in the same cog

- **`/account unlink` has no timeout branch.** `if view.value:` (`verify.py:83`) means a `None` from an expired
  confirmation sends nothing at all: the user sees a dead prompt and never learns whether anything happened.
- **Its failure text is a compound OR** — "You don't have a Minecraft account linked to your Discord account, or the
  unlinking failed" (`verify.py:104-107`) — which tells the user neither of the two things it might mean.
- **Ephemerality is incoherent.** The prompt and its cancellation honour `ephemeral=ctx.interaction is not None`
  (`verify.py:48`, `:56`), but the success that names the Minecraft account (`verify.py:73`) passes no `ephemeral`
  at all. You accept privately and get confirmed publicly. Same for unlink (`:80`, `:97`, `:110`).
- **Command descriptions are English-only.** Parameters use `app_commands.locale_str(_( … ))` (`verify.py:41`, `115`,
  `140`, `176`) but descriptions come from docstrings, which the extractor never sees. Thread 3765893624 is a
  complaint about the wording of one of them.
- **`consent.py:52` uses `t(None, …)`** for the ownership refusal, so it always renders in `DEFAULT_LOCALE`: the
  view takes a `locale` but never stores it on `self`.

### The consent notice is one 640-character msgid, and re-splitting it costs nothing

`squid/bot/consent.py` is 78 lines: a flat `discord.ui.TextDisplay` plus an `ActionRow`, with no `Container`, no
separators and no fields (`consent.py:25-43`). The whole notice is a single `_( … )` spanning four markdown blocks.

Both catalogues have that msgid **untranslated** — `locales/en/LC_MESSAGES/squid.po:54-69` and
`locales/zh_CN/LC_MESSAGES/squid.po:62-77` both have an empty `msgstr`. Splitting one large msgid into several small
ones normally throws away translation work; here there is none to throw away. This is worth stating so the split is
not deferred out of misplaced caution.

The reusable vocabulary for the replacement already exists and is used elsewhere: `card_container`, `CardField` and
`CardSection` (`squid/bot/utils/components.py:51-90`), with `squid/bot/diagnostics.py:82-105` as the model consumer
for localized fields. `card_container` returns a `discord.ui.Container`, which can be added to an *interactive*
`LayoutView` — `link_layout` (`components.py:147-151`) already builds a container plus an `ActionRow` exactly that
way. There are no classic embeds anywhere in `squid/bot`; Components V2 is the only pattern.

### Out of scope, found while auditing `verification_codes`

Recorded here because §1 adds columns to this table and a reader will be looking at it. Neither is a UX matter and
neither is fixed by this plan; both are filed in [`BUGS.md`](../../../BUGS.md).

- **`verification_codes.id` is a `SmallInteger` autoincrement primary key** (`infrastructure/models.py:227`) and
  rows are never deleted — `replace_verification_code` only flips `valid = False` and inserts a replacement
  (`repository.py:629-652`). The sequence therefore exhausts after 32,767 codes have ever been issued, after which
  every in-game `/link` fails. Nothing rewinds it.
- **There is no index on `verification_codes.code`.** The model has no `__table_args__`, so every redemption is a
  sequential scan. Harmless at current size, and §1 adds a second lookup per link.

## Subplans

### 1. A verification code you can hold

Give the code a two-step exchange — reserve, then commit or release — so the prompt can show what it is about to
do. A read-only peek was considered and rejected: a reservation is strictly better on three counts.

- The previewed code cannot be spent underneath the prompt, so the card cannot lie about which account it links.
- A reservation is a **write**, so it is countable and rate-limitable per caller. Plan 02 §6's attempt cap has
  somewhere to attach; a bare read would have handed out unlimited free guesses beside a capped path.
- It is the durable substitute for the transaction we cannot hold. The prompt waits on a human for 120 seconds; a
  database transaction cannot be held open across that, and a reservation row is what takes its place.

**Schema.** Add to `VerificationCode` (`squid/accounts/infrastructure/models.py:223`):

- `reserved_token: Mapped[str | None]` — the peppered digest of the reservation token, never the token itself.
- `reserved_until: Mapped[Instant | None]`.
- `CheckConstraint("(reserved_token IS NULL) = (reserved_until IS NULL)", name="verification_codes_reservation_complete")`,
  following the `accounts_consent_receipt_complete` idiom (`models.py:54-57`).

**The reservation is keyed on an opaque token, not on an account.** This is the load-bearing decision of the
subplan. `verify.py:61-63` creates the account only *after* consent, deliberately — "The account is created here
rather than by the redemption" — and the notice promises that cancelling "stores no user account information".
Keying a reservation on `account_id` would mint an account row in order to display a privacy prompt, and would make
that sentence false. With a token, the only write on the cancel path is to a `verification_codes` row that already
existed, holding a digest and a timestamp and identifying nobody.

**Three port methods** on `AccountRepository` (`squid/accounts/application/ports.py:39`):

- `reserve_verification_code(code, *, ttl) -> LinkReservation` — one transaction: `SELECT … FOR UPDATE` on the
  existing `expires > now`, `valid`, `code = hash(code)` predicate plus `(reserved_until IS NULL OR reserved_until
  <= now)`; mint a token; store its digest and `now + ttl`; return the token with the preview. No `valid = False`,
  no identity, no consent.
- `consume_reservation(*, account_id, code, reservation_token, consent)` — the existing
  `consume_code_and_link_account` body, with `reserved_token = hash(token)` and `reserved_until > now` added to the
  predicate. Everything is re-checked under the lock, because the *world* can change even though the code cannot.
- `release_verification_code(*, code, reservation_token)` — clears both columns when the token matches; idempotent,
  so the cancel and timeout paths can both call it.

Derive the `ttl` from the view's `timeout` rather than writing 120 seconds in two places.

**Crash safety needs no sweeper.** If the process dies between reserve and release, `reserved_until` lapses and the
code frees itself. A guessed-code reservation can block the legitimate owner for one TTL, and that self-heals
too: an in-game `/link` calls `replace_verification_code`, which invalidates prior codes and issues a fresh one.

**A lapsed prompt must not masquerade as a bad code.** Add `LINK_RESERVATION_EXPIRED` to `ErrorCode`
(`squid/core/errors.py:12-64`) and a `LinkReservationExpiredError(ValidationError)` whose `end_user_action` is to
run `/account link` again with a fresh code. Reusing `InvalidVerificationCodeError` here would tell a user their
correct code was wrong.

**Ordering dependency on plan 02 §6.** The token digest must use whatever `hash_verification_code`
(`repository.py:410`) becomes there — HMAC, not a prefixed SHA-256 — and reservation attempts must count against
that subplan's failure cap, or the reservation path bypasses the cap entirely. Land plan 02 §6 first, or accept
that this subplan reopens it.

### 2. The consent prompt as a card with a real preview

Rebuild `UserDataConsentView` on `card_container` + `CardField`, replacing the single `TextDisplay`. Fields:

| Field | Value |
|---|---|
| Minecraft account | `{username}` · `{java_uuid}` |
| Discord account | the caller, plus their ID |
| Build credit | name, build count, and whether another creator already holds it |
| Consent receipt | notice version, recorded at the moment of agreement |

Keep the full notice reachable through a third secondary **Privacy notice** button that replies ephemerally, so the
long text stops competing with the summary. The card must still name the stored categories in one short line: the
point of the notice is informed consent, and consent is not informed if every category is behind a button. That
line is what satisfies "simplify"; the fields are what satisfy "add a preview?".

Store the `locale` on the view and use it for the ownership refusal, fixing `t(None, …)` at `consent.py:52`.

**Reorder `/account link`** so failures happen before the prompt, not after it:

1. Read the account by Discord identity **without creating it** — `get_account_by_identity`, as `unlink` already
   does at `verify.py:86`.
2. If it already holds a *different* Java identity, raise `AccountAlreadyLinkedError` now. This check needs the
   caller's account and so cannot live in the anonymous reservation; today it fires after consent.
3. Reserve the code. A bad code fails here, before any prose is read.
4. Show the card and wait.
5. Agree → `account_id_for(...)` (creating the account, exactly as today) → `consume_reservation` with the consent.
6. Cancel or timeout → `release_verification_code`.

### 3. One state-transition vocabulary

Render link's confirmation through the existing `_refresh_message`, so link and refresh describe the same
reconciliation in the same words. This has a second effect worth the change on its own: **it makes a stale preview
self-correcting.** If the credit became claimed between preview and agreement, `_reconcile_java_name` already
refuses to transfer it and opens a contested claim, and `_refresh_message` already renders that branch — so the
divergence explains itself instead of going silent, which is the failure mode the preview would otherwise
introduce.

Then close the smaller gaps: add the missing `view.value is None` branch to `unlink`, split its compound-OR failure
into "you had nothing linked" and "unlinking failed", and apply one ephemerality rule across the cog — user-facing
confirmations (`link`, `unlink`, `refresh`, `claim`) follow the prompt's `ephemeral=ctx.interaction is not None`,
while `approve-claim` and `reject-claim` stay public so the staff channel keeps a visible audit trail of who was
credited.

### 4. Name the claimant everywhere a claim is presented

Promote `_claimant` to a localized `present_claimant`, adding the public creator identity and marking the
`account {id}` branch explicitly as a diagnostic fallback rather than a name. Then give all four surfaces the same
string:

- **Approve and reject**: load the claimant on `resolve_claim`'s return through the existing `_load_accounts`
  (`repository.py:686-707`), so the confirmations can name them without reintroducing a per-claim query.
- **Autocomplete**: widen the `PendingAliasClaims` protocol (`records.py:80-83`) to accept `with_claimants` and
  build `description` from `present_claimant`. Discord truncates descriptions at 100 characters; budget for it.
- **The queue**: render `/account claims` as a card with localized rows carrying claim ID, alias name, claimant and
  age. `card_container` budgets against `MAX_DISPLAY_CHARACTERS` already, but silently truncating a review queue is
  wrong — cap the rows explicitly and say how many are not shown.

Rewrite `reject_claim`'s description (thread 3765893624) to say what it does rather than what it does not:
closing a claim without crediting the name. Record the docstring-descriptions i18n gap as a follow-up rather than
fixing it here — it is every command in the tree, not this cog.

### 5. Conflict context on alias errors

`AliasAlreadyClaimedError` gains keyword-only `holder_public_creator_id` and `holder_account_id`:

- `public_context`: `name` and `public_creator_id`. A creator profile is public data — that is what
  `accounts.public_creator_id` is for, and `GET /v1/creators/{creator_id}` (`squid/api/v1/users.py:28`) serves it
  unauthenticated — so naming the holder is disclosure of something already public.
- `context`: the internal `holder_account_id`, for logs only. `_safe_log_context` (`squid/bot/errors.py:94-96`)
  already strips Discord-ID-shaped keys; no Discord identifier goes into either dict.

Resolve the holder to a display name in the *service*, with `get_creator_profile` plus the existing `with_context`
(`squid/core/errors.py:136-154`, which exists for exactly this and refreshes `args`). That keeps the extra query on
an error path only, and keeps `build_error_presentation` synchronous.

Give the two raise sites different next actions through the per-instance `end_user_action` the base class already
accepts (`core/errors.py:77-99`) — ask staff to review it, versus approve with `reassign: True`, a parameter that
already exists at `verify.py:180`. No new error class and no new `ErrorCode`.

## Interfaces and Tests

### The reservation and its preview

```python
@dataclass(frozen=True, slots=True)
class CreditPreview:
    """The creator credit a link is about to affect."""

    name: str
    build_count: int
    held_by_public_creator_id: UUID | None = None
    """`None` means unclaimed, so agreeing attributes it to the caller.

    Set means another creator holds it: agreeing opens a staff claim and moves nothing, which is
    what `_reconcile_java_name` already does and what the card has to say up front.
    """


@dataclass(frozen=True, slots=True)
class LinkPreview:
    """What a held code will do, knowable without spending it."""

    java_uuid: UUID
    username: str
    credit: CreditPreview | None = None
    """`None` when no build credits this name yet, so there is nothing to move."""

    java_uuid_held_elsewhere: bool = False


@dataclass(frozen=True, slots=True)
class LinkReservation:
    """A held code, plus the one-time token needed to commit or release it."""

    token: str
    expires_at: Instant
    preview: LinkPreview
```

`java_uuid_held_elsewhere` is a fact about the *code*, so the anonymous reservation can report it. "You already
linked a different Minecraft account" is a fact about the *caller*, so it stays a pre-flight check in the cog
(§2 step 2). Build counts come from `build_creators.alias_id` (`squid/builds/infrastructure/models.py:276-278`), one
aggregate.

### Tests

- **Consent card payload** (`tests/unit/bot/test_components_v2_ui.py`): the card names the stored categories, the
  previewed username, UUID and credit, and the notice version; Cancel leaves `view.consent is None`; the Privacy
  notice button commits nothing. The existing
  `test_user_data_consent_view_discloses_storage_and_actions` (`:179-188`) asserts `payload[0]["content"]`
  directly and so hard-codes the flat, container-less shape — **rewrite it deliberately rather than patching the
  substrings**, and assert the container structure instead of a single blob.
- **Reservation** (`tests/integration/accounts/`): reserve then commit writes the identity, consent and
  reconciliation exactly once; reserve then release frees the code immediately; a lapsed `reserved_until` frees it
  with no sweeper; a second reserve against a held code is refused; committing with a wrong or lapsed token raises
  `LinkReservationExpiredError` and not `InvalidVerificationCodeError`; two callers racing one reserve produce one
  winner.
- **The cancel path stores nothing about the user** — the promise the notice makes. After reserve then release,
  assert zero rows added to `accounts`, `account_identities` and no consent receipt anywhere. This is the test that
  keeps the token-keyed design from being "simplified" into an account-keyed one later.
- **Preview honesty**: a credit claimed between preview and commit yields the contested branch in the confirmation,
  proving the divergence is reported rather than swallowed.
- **Message rendering** (`tests/unit/bot/test_verify_messages.py`): link rendered through `_refresh_message` for
  every branch it can reach, the unlink timeout branch, and both halves of the split failure message. The existing
  "no combination produces an empty message" parameterization (`:79-97`) is the pattern to extend — Discord rejects
  an empty message outright.
- **Claimant presentation**: mention, Java-IGN fallback and diagnostic `account {id}` fallback, and the *same*
  string from all four surfaces (queue, approve, reject, autocomplete). Assert the autocomplete description stays
  within 100 characters for a long IGN.
- **Query counts**: extend `test_account_query_counts.py` so naming the claimant in approve/reject and in the
  autocomplete adds a constant number of statements, not one per claim.
- **Error payloads**: `AliasAlreadyClaimedError.public_context` carries `name` and `public_creator_id` and never an
  internal account ID or any Discord identifier, asserted through both renderers — `build_error_presentation`
  (`squid/bot/errors.py:173-233`) and the RFC 9457 mapping (`squid/api/errors.py:184-197`). Assert the two raise
  sites produce different `end_user_action` text.
- **i18n**: the queue row and every new card field go through `t(...)`; `just i18n-extract` leaves no new
  untranslated literal in the cog, and the ownership refusal honours a non-default locale.

### Before merging

`alembic heads` must show the single new revision. The migration only adds two nullable columns and one check
constraint, so `test_migrations_create_schema_without_drift` covers both directions without a bespoke round-trip
test. Reservations are transient by construction — codes expire in ten minutes — so the downgrade needs no data
migration.

## Disposition

| Thread | Disposition |
|---|---|
| 3765772656 — "simplify" | **Fix.** The notice becomes a short categories line plus labelled fields, with the full text behind a Privacy notice button. Read with 3765867741, "simplify" is not answered by re-flowing prose. |
| 3765867741 — "add a preview?" | **Fix.** Not implementable when the comment was written, and that is the finding: the two-step reservation in §1 is what makes a concrete preview possible at all. |
| 3765882882 — "not user friendly" | **Fix.** The queue becomes a localized card with claimant and claim age, replacing an untranslated f-string. The claimant half of the complaint was already partly answered by plan 02 §5. |
| 3765888733 — "ux, who is claimant" | **Fix.** `resolve_claim` returns the claimant, so approve and reject name them instead of saying "the claimant". One `present_claimant` serves all four surfaces. |
| 3765893624 — "confusing for no reason" | **Fix.** It is a slash-command description, not an internal docstring. Rewritten to state what the command does. The tree-wide docstring i18n gap is recorded as a follow-up, not fixed here. |
| 3765913970 — "ux" | **Fix.** Folded into the one state-transition vocabulary in §3, so rejection reads the same way as every other resolution. |
| 3766207128 — "context about which account claimed it" | **Fix.** `public_context` gains the holder's public creator ID and a resolved creator name; the internal account ID goes to `context` for logs only. Public because creator profiles are public — and see the `TODO.md` entry, because nothing in Discord can currently look one up. |

## Delivery

1. `accounts: let a verification code be held before it is spent` — §1, with the migration and the reservation
   tests. Lands after plan 02 §6.
2. `bot: preview the link a consent prompt is asking for` — §2.
3. `bot: describe link and refresh in the same words` — §3.
4. `bot: name the claimant wherever a claim is shown` — §4. Independent of 1–3.
5. `accounts: name the creator holding a contested alias` — §5. Independent of 1–4.

Replying on GitHub and resolving threads requires separate explicit authorization, per the
[directory README](README.md).
