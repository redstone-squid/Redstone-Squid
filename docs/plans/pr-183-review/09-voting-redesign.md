# PR #183 Review: Voting Redesign

## Findings

The 34 voting-related review comments on [PR #183](https://github.com/redstone-squid/Redstone-Squid/pull/183) at or before the
`5edfd3e` cutoff address domain modeling, persistence invariants, transport neutrality, Discord UI ergonomics, session
lifecycle abstractions, and test maintainability:

- **Domain Model & Typed Targets**:
  - The branch introduced application-owned vote sessions, stable option identifiers, typed choices (`VoteChoice`), and
    ballot-safe API reads/writes. However, `VoteTarget` still conflates build targets (`build_id`), message deletion targets
    (`message_id`, `channel_id`, `server_id`), and generic polls with an untyped bag of nullable fields.
  - Target data must be split into strongly typed, distinct domain models (`BuildVoteTarget`, `DeleteLogVoteTarget`), while
    generic polls own their metadata (`GenericPoll`) without an external target.
  - `VoteKind`, `VoteStatus`, `VoteSessionResult`, `VoteVisibility`, and `VoteRejection` remain string literal type aliases.
    They should be promoted to first-class `StrEnum`s across domain, persistence, application, API, and bot layers, eliminating
    the string casts and assertions currently used to compensate for untyped persistence strings.

- **Persistence Invariants & Sentinel Thresholds**:
  - Generic polls currently store sentinel thresholds `32767` (`pass_threshold`) and `-32768` (`fail_threshold`) in
    `vote_sessions` to satisfy legacy `NOT NULL` and sign check constraints, even though generic polls never threshold-close.
  - `pass_threshold` and `fail_threshold` must become nullable (`int | None`). Sentinel values must be migrated to `NULL`,
    and database check constraints must enforce that threshold votes (`build`, `delete_log`) require non-null integer
    thresholds (`pass > 0`, `fail < 0`), while generic polls require `NULL` thresholds.
  - `generic_vote_sessions.guild_id` is currently `NOT NULL` with a foreign key to `server_settings.server_id`, enforcing
    that generic polls are owned by a Discord guild. Making `guild_id` nullable allows transport-independent poll creation
    (e.g., via REST API or standalone drafts) before attaching Discord presentation messages.

- **Transport-Independent Polls & Decoupled Publication**:
  - Generic poll creation is currently coupled to Discord: `start_generic_vote` requires a `guild_id`, the wizard passes
    the full `VoteCog`, and publication sends a Discord message inline during creation.
  - Poll creation and publication must be separated: an application service method creates a draft/session independently
    of presentation locations, and presentation messages are attached idempotently via an explicit attachment contract.
  - Partial publication failures (e.g., Discord API outage or missing bot permissions in one channel) must leave the database
    session intact and attachable without leaving phantom or orphaned records.
  - Option identifiers must remain stable and transport-neutral, while per-location emoji mappings stay in the Discord adapter.

- **Discord Command Taxonomy & UI Component Wizard**:
  - The Discord interface remains awkward: `/vote poll` relies on raw text inputs in a modal for `duration` (e.g. `24h`),
    `visibility` (e.g. `anonymous_live`), and manual `emoji | label` parsing, which is error-prone.
  - Poll commands should be organized under a dedicated `/poll` hybrid group (`/poll create`, `/poll close`, `/poll refresh`),
    keeping `/vote delete` for moderation votes.
  - Modern Discord UI components (Select menus and Button selectors) must replace free-text inputs for visibility modes
    (`Live Anonymous`, `Live Public`, `Hidden until Close`) and duration presets (`1h`, `6h`, `12h`, `24h`, `3d`, `7d`, `Custom`).
  - `PollModal` and `PollConfirmation` must interact with a narrow `PollPublisher` application facade instead of taking the
    entire `VoteCog` instance.
  - Domain/application policies must explicitly govern anonymity, reaction removal, and closure authorization rather than
    inlining checks in Discord event listeners. Typed `VoteRejection` outcomes must be localized via `t(locale, _(...))`.

- **Session Presentation Cleanup & Discord-State Recovery**:
  - `AbstractVoteSession` in `squid/bot/voting/base_session.py` contains fragile metaprogramming (`_allow_init` boolean flag,
    `__init_subclass__` signature reflection, and parallel `_votes` state). `GenericVoteSession` already proved that wrapping
    `VoteSessionSnapshot` directly is cleaner.
  - `BuildVoteSession` and `DeleteLogVoteSession` must be refactored into thin presentation wrappers around `VoteSessionSnapshot`,
    retiring `AbstractVoteSession`.
  - Presentation reconciliation must handle Discord edge cases gracefully: deleted messages (`discord.NotFound`), missing
    permissions (`discord.Forbidden`), and reaction clears (`on_reaction_clear`, `on_reaction_clear_emoji`). Safe presentation
    reactions should be restored without attempting to infer discarded anonymous ballots.
  - Periodic poll expiration checks and due-closing must be routed through `BackgroundTaskSupervisor` in `squid/runtime.py`.

- **Test Maintainability & Coverage**:
  - `tests/integration/voting/infrastructure/test_vote_repository.py` contains over 500 lines with repetitive setup logic.
    Common fixtures, session builders, and poll seed helpers must be extracted.
  - Direct SQL in tests must be restricted to verifying database-level constraints or concurrency contracts that cannot
    be surfaced through repository APIs.

---

## Subplans

1. **Domain and persistence model**
   - Split `VoteTarget` into strongly typed, distinct domain models:
     - `BuildVoteTarget(build_id: int)`
     - `DeleteLogVoteTarget(message_id: int, channel_id: int, server_id: int)`
     - `type VoteTarget = BuildVoteTarget | DeleteLogVoteTarget | None`
   - Make `GenericPoll` independent of guild requirements (`guild_id: int | None = None`).
   - Promote `VoteKind`, `VoteStatus`, `VoteSessionResult`, `VoteVisibility`, and `VoteRejection` to `StrEnum`s.
   - Make `vote_sessions.pass_threshold` and `fail_threshold` nullable (`int | None`). Migrate existing sentinel values
     (`32767` / `-32768`) to `NULL`.
   - Update database check constraints to reject invalid kind/target/threshold combinations at the schema boundary.
   - Make `generic_vote_sessions.guild_id` nullable to support transport-neutral poll sessions.

2. **Transport-independent polls and decoupled publication**
   - Add application service methods (`create_generic_poll`) that instantiate poll sessions independently of Discord guilds
     or publication channels.
   - Introduce an idempotent message attachment operation (`attach_message`) on `VoteRepository` and `VoteService`.
   - Ensure partial publication or Discord API failures allow safe retry and reconciliation without duplicating sessions.
   - Preserve stable option IDs across all transports while scoping emoji aliases to Discord presentation messages.

3. **Discord command and UI redesign**
   - Introduce a dedicated `/poll` hybrid command group:
     - `/poll create`: Launches the interactive component-based wizard.
     - `/poll close <message>`: Closes a poll early (creator or authorized staff).
     - `/poll refresh <message>`: Recomputes cached role weights.
   - Keep `/vote delete <message>` for moderation deletion votes and deprecate `/vote poll` with a redirect alias.
   - Redesign the poll wizard with interactive Discord UI components:
     - Select menu for visibility (`anonymous_live`, `visible_live`, `anonymous_hidden`) with descriptive labels.
     - Select menu / button presets for duration (`1h`, `6h`, `12h`, `24h`, `3d`, `7d`, `Custom...`).
     - Structured option input with fallback palette resolution and inline validation.
   - Extract a `PollPublisher` protocol/facade to replace direct `VoteCog` references in UI views.
   - Move reaction removal and visibility decisions into named domain/application methods (`snapshot.is_anonymous`,
     `session.should_remove_reaction_on_cast()`, `session.can_close(actor)`).
   - Render all typed `VoteRejection` values into localized user messages via `t(locale, _(...))`.

4. **Session presentation cleanup and Discord-state recovery**
   - Retire `AbstractVoteSession`, `_allow_init` flags, and `__init_subclass__` reflection from `base_session.py`.
   - Standardize `BuildVotePresentation`, `DeleteLogVotePresentation`, and `GenericVotePresentation` as thin view adapters
     that wrap `VoteSessionSnapshot` and implement `.render()`, `.update_messages()`, and `.add_reactions()`.
   - Gracefully handle `discord.NotFound` and `discord.Forbidden` during message updates and reaction operations.
   - Handle `on_reaction_clear` and `on_reaction_clear_emoji` by restoring configured baseline reaction options without
     guessing or reconstructing anonymous ballots.
   - Route deadline polling and due-closing through `BackgroundTaskSupervisor`.

5. **Test modernization and cleanup**
   - Extract shared repository fixtures, test session builders, and poll seed helpers into `tests/helpers/voting.py`.
   - Remove redundant hand-rolled SQL from integration tests, keeping direct SQL queries strictly where asserting DB
     check constraints, foreign key cascades, or concurrency locking (`pg_advisory_xact_lock`, `with_for_update`).
   - Parameterize kind/visibility/target test matrices across domain, application, and API test suites.

---

## Interfaces and Tests

### Target & Enum Interfaces

```python
class VoteKind(StrEnum):
    BUILD = "build"
    DELETE_LOG = "delete_log"
    GENERIC = "generic"

class VoteStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"

class VoteSessionResult(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"
    PENDING = "pending"

class VoteVisibility(StrEnum):
    ANONYMOUS_LIVE = "anonymous_live"
    VISIBLE_LIVE = "visible_live"
    ANONYMOUS_HIDDEN = "anonymous_hidden"

class VoteRejection(StrEnum):
    NOT_FOUND = "not_found"
    CLOSED = "closed"
    NOT_ELIGIBLE = "not_eligible"
    INVALID_OPTION = "invalid_option"
    WRONG_GUILD = "wrong_guild"
    NOT_AUTHORIZED = "not_authorized"

@dataclass(frozen=True, slots=True)
class BuildVoteTarget:
    build_id: int

@dataclass(frozen=True, slots=True)
class DeleteLogVoteTarget:
    message_id: int
    channel_id: int
    server_id: int

type VoteTarget = BuildVoteTarget | DeleteLogVoteTarget | None
```

### Publisher Facade Protocol

```python
class PollPublisher(Protocol):
    """Facade providing poll creation and Discord message attachment for UI wizards."""

    async def create_and_publish(
        self,
        *,
        author_discord_id: int,
        channel: GuildMessageable,
        question: str,
        visibility: VoteVisibility,
        duration_seconds: int,
        options: Sequence[VoteOption],
    ) -> discord.Message: ...
```

### Test Suite Matrix

- **Domain Tests** (`tests/unit/voting/test_dynamic_voting.py`):
  - Matrix across all `VoteKind`, `VoteTarget`, `VoteVisibility`, `VoteChoice`, and `VoteRejection` values.
  - Domain validation rejecting non-null thresholds for generic polls and null/invalid thresholds for builds and log deletions.
  - Raw vs. weighted tally computations and anonymous tally hiding invariants.
- **Persistence & Migration Tests** (`tests/integration/voting/infrastructure/test_vote_repository.py`, `tests/integration/test_alembic_migrations.py`):
  - Migration upgrade and downgrade verifying sentinel value migration to `NULL` and constraint application.
  - Concurrency tests verifying row-level locks, concurrent vote casting, weight refresh, and threshold close races.
  - Attachment and multi-message tracking tests with optional `guild_id`.
- **Application & Service Tests** (`tests/unit/voting/application/test_vote_service.py`):
  - Create-before-publish flows, idempotent message attachment, and partial publication failure recovery.
  - Role-weight calculation, staff multiplier fallbacks, and capability checks (`VOTE_LOG_DELETE_CAST`, `VOTE_POLL_CLOSE_ANY`).
  - Expired poll discovery and atomic due-closing via `close_due()`.
- **Discord Presentation & UI Tests** (`tests/unit/bot/voting/`):
  - Component select interactions, duration preset parsing, and option validation.
  - Error localization for all rejection outcomes.
  - Deleted message (`discord.NotFound`), missing permission (`discord.Forbidden`), and reaction clear recovery.
- **REST API Tests** (`tests/unit/api/test_vote_writes.py`, `tests/unit/api/test_phase2_reads.py`):
  - Ballot privacy guarantees (withholding tallies for open `anonymous_hidden` polls).
  - Voting via stable `option_id` across distinct guild aliases.

---

## Disposition

| Category | Topics / Review Threads | Disposition |
|---|---|---|
| **Domain Models & Enums** | Untyped string literals (`VoteRejection`, `VoteKind`, `VoteStatus`, `VoteVisibility`), conflated `VoteTarget` fields, rigid generic poll guild coupling | **Fix.** Introduce `StrEnum`s, split `VoteTarget` into typed models (`BuildVoteTarget`, `DeleteLogVoteTarget`), make `GenericPoll.guild_id` optional. |
| **Persistence & Thresholds** | Sentinel thresholds (`32767/-32768`) in `vote_sessions`, missing schema check constraints for generic polls | **Fix.** Migrate sentinels to `NULL`, make columns nullable, and add database check constraints enforcing kind/threshold rules. |
| **Transport Independence** | Creating polls requiring immediate guild and message publication, tight coupling to Discord | **Fix.** Decouple session creation from publication, add idempotent `attach_message`, and allow guild-independent polls. |
| **Discord UI & Commands** | `/vote poll` command name, raw text inputs in modals for visibility/duration, `VoteCog` coupling in modals, unlocalized errors | **Fix.** Implement `/poll` group, interactive Select menu components for visibility/duration, `PollPublisher` facade, and localized rejection strings. |
| **Session Architecture** | `AbstractVoteSession` reflection, `_allow_init` flag, `__init_subclass__` metaprogramming, in-memory duplicate vote tracking | **Fix.** Retire `AbstractVoteSession`; standardize thin snapshot-wrapping presentation classes across all vote kinds. |
| **Discord Recovery** | Missing messages, reaction clears, permission loss, unhandled exceptions during background fan-outs | **Fix.** Graceful handling of `discord.NotFound`/`discord.Forbidden`, safe reaction re-adding, and `BackgroundTaskSupervisor` deadline scheduling. |
| **API & Ballot Privacy** | API routes exposing aggregate tallies, privacy for hidden polls, stable option ID writes | **Already fixed** in `5edfd3e` and retained; strengthen with typed DTO mappings and enum validation. |
| **Test Fixtures** | 80-line hand-written DDL in `test_vote_repository.py`, oversized integration test cases | **Already fixed** in `16eb510` and improved; extract reusable session/poll builders in `tests/helpers/voting.py`. |

---

## Verification status (as of `d2341c80`, 2026-08-18)

All of the Findings and Subplans above are now stale as description-of-work-remaining; they are
kept verbatim because the Disposition table still points at them. What follows is the current
state, checked against actual code and tests, not the plan text.

- **Subplans 1-4: done.** `509406c2` landed the bulk of it (`StrEnum`s for `VoteKind`,
  `VoteStatus`, `VoteSessionResult`, `VoteVisibility`, `VoteRejection`; `BuildVoteTarget` /
  `DeleteLogVoteTarget` / `GenericPoll` in `squid/voting/domain/models.py`; nullable
  `pass_threshold`/`fail_threshold` with a check constraint enforcing kind/threshold combinations,
  verified in `tests/integration/voting/infrastructure/test_vote_repository.py::test_the_schema_rejects_kind_threshold_combinations_the_domain_forbids`;
  the `/poll` wizard; `PollPublisher` in `squid/bot/voting/publisher.py`). `base_session.py` and
  `AbstractVoteSession` no longer exist anywhere in `squid/` or `tests/`. `close_due` is scheduled
  via `self._supervisor.start_periodic(self._close_due_votes, ...)` in `squid/worker/app.py`, so it
  runs under `BackgroundTaskSupervisor` (`squid/runtime.py`) as required.
  - The presentation layer went further than subplan 4 described: rather than per-kind thin
    wrapper classes (`BuildVotePresentation`, `DeleteLogVotePresentation`, `GenericVotePresentation`),
    `squid/bot/voting/sessions.py` now only *creates* sessions, and rendering/reconciliation was
    generalized into `squid/bot/posts/reconciler.py` and `squid/bot/posts/vote_renderer.py`, shared
    with non-voting post kinds. `discord.NotFound`/`discord.Forbidden` are handled there and in
    `squid/bot/voting/vote.py`'s `_restore_reactions` (used for both `on_reaction_clear` and
    `on_reaction_clear_emoji`), which re-adds configured baseline reactions without attempting to
    infer discarded ballots, matching the subplan's intent even though the class names differ.
  - Later voting-touching commits after `509406c2` (`63c5918a` resolve vote actors by account,
    `9476415d` match delete-log cards on enum members, `1ce791c4` treat a missing vote channel as a
    setup gap, `5061234e` stop minting vote foreign keys from sequences, `37add609` resolve Discord
    members through discord.py's rate limiter) are incidental hardening, not new redesign scope, and
    don't change this disposition.

- **Subplan 5: partially done, not "unverified."** Checked directly against the test files named
  in the Test Suite Matrix:
  - `tests/helpers/voting.py` exists and is used by all four suites (`build_snapshot`,
    `poll_snapshot`, `attach_vote_message`, `seed_delete_log_vote`) — the fixture-extraction bullet
    is done, and per plan 13's disposition table (`3775316974`, `3775329634`), so is the DDL/helper
    extraction in `test_vote_repository.py` (`tests/helpers/schema.py`, `seed_generic_poll`).
  - Direct SQL remaining in `test_vote_repository.py` is scoped correctly: root-count sanity checks,
    one read-back of sentinel-vs-NULL thresholds ("the domain refuses to represent the sentinels at
    all"), and the parametrized `test_the_schema_rejects_kind_threshold_combinations_the_domain_forbids`
    which asserts a DB constraint the repository can't itself produce. No hand-rolled setup SQL that
    the repository could exercise instead was found.
  - The "matrix across all `VoteKind`, `VoteTarget`, `VoteVisibility`, `VoteChoice`, and
    `VoteRejection` values" is **not** a real cross-product matrix anywhere. What exists is scattered,
    single-axis `@pytest.mark.parametrize` (e.g. `kind` over `[BUILD, DELETE_LOG]` in
    `test_dynamic_voting.py`'s threshold/anonymity tests, `visibility` over the two anonymous modes,
    `(kind, pass_threshold, fail_threshold)` triples in `test_vote_repository.py`). `GENERIC` is
    largely exercised only through separate non-parametrized tests (`poll_snapshot`-based), not
    folded into the same parametrized cases as `BUILD`/`DELETE_LOG`. `tests/unit/api/test_vote_writes.py`
    has zero `@pytest.mark.parametrize` uses; its option-id/guild-alias coverage is one test per
    scenario rather than a matrix. No commit in `git log` mentions parametrization or a test matrix
    for voting, confirming this bullet was never done, not just done-and-unverifiable.
  - Net: subplan 5's first two bullets (fixture/builder extraction, SQL scoping) are done; the third
    (kind/visibility/target matrix parameterization) is not started as a deliberate cross-product,
    though incidental single-axis parametrization exists.

**Overall disposition: Mostly done.** Subplans 1-4 (domain typing, transport-independent polls,
`/poll` UI, session/recovery cleanup) are complete and verified against current code, including
`close_due` running under `BackgroundTaskSupervisor`. Subplan 5's fixture-extraction and SQL-scoping
bullets are done; only the kind/visibility/target matrix-parameterization bullet remains open, and it
has not been started as a systematic cross-product (only ad hoc single-axis parametrization exists).
