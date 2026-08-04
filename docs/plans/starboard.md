# Starboard, on a shared reaction kernel

> **Status.** Approved, implementation in progress. Amend this document in place as building it
> proves parts of it wrong, calling out the amendments where they occur rather than silently
> applying them.

> **Implementation note (2026-08-04).** Step 0 had already landed as commit `b0d081c` before
> implementation of this plan began; the architecture regression described below no longer
> reproduces.

## Context

We already run one reaction-driven scoring feature: vote sessions (`squid/voting`), which since
commit `65dd01c` cover build approval, delete-log votes, and user-created generic polls. A
starboard is the same shape of thing — reactions accumulate on a message, a weighted score
crosses a threshold, and the bot materialises something — but it is not the same aggregate, and
the difference matters more than the similarity.

We are building it in-house rather than adding [CircuitSacul/Starboard-4][sb4] because the
stored data is the point: cross-server broadcasting and leaderboards/analytics need the vote
rows, their weights, their timestamps, and their target authors in *our* database, joinable
against builds, users, and records.

[sb4]: https://github.com/CircuitSacul/Starboard-4

### What polls and starboards actually share

Worth sharing:

- **Actor facts.** `VoteActor` (`squid/voting/domain/models.py:77`) — user id, guild, role ids,
  staff/trusted flags — is exactly what a starboard needs to weight and authorise a star.
- **Role-weight policy.** `RoleVoteWeightPolicy`
  (`squid/voting/application/policies.py:13`) takes the highest configured role multiplier with
  a 3× staff fallback. That rule is not voting-specific; only its eligibility check
  (`kind == "delete_log"` requires trusted) is.
- **Actor resolution.** `VoteActorResolver` (`squid/voting/application/ports.py:27`) resolves
  current member facts so cached weights can be recomputed after a role change. A starboard
  recount needs the identical port, and `VoteCog.resolve` (`squid/bot/voting/vote.py:291`) is
  already the implementation.
- **Raw reaction plumbing.** `VoteCog.update_vote_sessions` (`squid/bot/voting/vote.py:70`) and
  `AdminCog.remove_archived_message` (`squid/bot/admin.py:184`) each own an
  `on_raw_reaction_add` listener and each independently fetches the channel, message, and
  member. A third copy for starboards would mean three fetches per reaction.

Not worth sharing, and forcing them together would be a mistake:

| | Poll | Starboard |
|---|---|---|
| Lifecycle | Aggregate created first, message sent second | Lazily materialised; *any* message may accumulate votes |
| Cardinality | One session per message | One reaction fans out to N starboard configs |
| Selection | One exclusive, togglable choice per user | One vote per user *per starboard* |
| Terminal state | Closes permanently at threshold or deadline | Never terminal; posts and un-posts with hysteresis |
| Target | Owns its own message | Mirrors somebody else's, and must track its edits and deletion |

So: extract the small kernel, leave `vote_sessions`/`votes` alone, and give starboards their own
tables and service.

## Decisions taken

| Question | Answer |
|---|---|
| Abstraction | Shared kernel (`squid/reactions`) + a bot-layer reaction router; voting's tables and public names unchanged |
| Cross-guild | Schema shaped for it now (`starboard_sources` + explicit source-guild grant); v1 commands stay single-guild |
| v1 scope | Core starboard + weighted votes |
| Webhooks | No. Starboard-4's `use_webhook` conflicts with the Components V2 rule this repo enforces |
| Deferred | Channel allow/deny lists and per-channel overrides; freeze/trash/force moderation; leaderboard commands; autostar channels; exclusive groups; XP roles; filters |

## Step 0 — Repair the architecture suite first

`tests/architecture/test_discord_components_v2.py` **currently fails** on four sites introduced
by `65dd01c`:

```
squid/bot/voting/poll_wizard.py:91,142,152   legacy message fields ['content']
squid/bot/voting/poll_wizard.py:99           discord.ui.View
squid/bot/voting/generic_session.py:44       legacy message fields ['content']
```

Convert those to `StaticLayout`/`text_layout` (`squid/bot/utils/components.py`) so starboard work
does not land on a red suite. Separate commit: `voting: render polls with components v2`.

## Step 1 — Extract the reaction kernel

New context `squid/reactions/` following the repo's domain/application layering.

**`squid/reactions/domain/models.py`**

```python
@dataclass(frozen=True, slots=True)
class ReactionActor:
    """Framework-neutral member facts used to authorise and weight a reaction."""
    user_id: int
    guild_id: int = 0
    role_ids: frozenset[int] = frozenset()
    is_staff: bool = False
    is_trusted: bool = False


@dataclass(frozen=True, slots=True)
class WeightScope:
    """The configuration bucket a weight lookup belongs to."""
    guild_id: int
    kind: str                    # "build" | "delete_log" | "generic" | "starboard"
    scope_id: int | None = None  # starboard id; None for the guild-wide vote kinds


@dataclass(frozen=True, slots=True)
class RoleMultiplier:
    scope: WeightScope
    role_id: int
    multiplier: float
```

`RoleMultiplier.__post_init__` keeps the finite-and-positive validation currently on
`RoleWeight`.

**`squid/reactions/application/ports.py`** — `WeightPolicy` (`calculate(actor, scope) -> float |
None`), `ActorResolver` (`resolve(user_id, guild_id, scope) -> ReactionActor | None`),
`RoleMultiplierProvider`.

**`squid/reactions/application/policies.py`** — `RoleWeightPolicy`, generalised from
`RoleVoteWeightPolicy`: constructor takes the multiplier provider, an optional
`eligibility: Callable[[ReactionActor, WeightScope], bool]`, and `staff_multiplier: float = 3.0`.

**Voting keeps its names.** `squid/voting/domain/models.py` aliases `VoteActor = ReactionActor`
and re-exports it; `RoleVoteWeightPolicy` becomes a thin adapter that maps
`(actor, session, emoji)` onto `(actor, WeightScope(...))` and supplies the delete-log
eligibility predicate. `guild_vote_role_weights` is **not** migrated — the shared piece is the
rule, not the table. `VoteService` and every call site are untouched.

Commit: `reactions: extract a shared reaction weighting kernel`. Verify with
`pytest tests/unit/voting tests/architecture` — no behaviour should change.

## Step 2 — One raw-reaction dispatcher

**`squid/bot/reactions.py`**

```python
@dataclass(frozen=True, slots=True)
class ReactionEvent:
    payload: discord.RawReactionActionEvent
    emoji: str
    member: discord.Member | None
    async def message(self) -> discord.Message | None: ...   # memoised fetch


class ReactionSubscriber(Protocol):
    async def on_reaction_add(self, event: ReactionEvent) -> None: ...
    async def on_reaction_remove(self, event: ReactionEvent) -> None: ...
```

`ReactionRouter` is a plain object constructed in `RedstoneSquid.__init__` and exposed as
`bot.reactions`, so extension load order does not matter. A `ReactionRouterCog` owns the only
`on_raw_reaction_add` / `on_raw_reaction_remove` / `on_raw_reaction_clear` /
`on_raw_reaction_clear_emoji` listeners in the bot, resolves member and message **once**, and
dispatches to each subscriber in its own task with per-subscriber error isolation, so one
subscriber raising cannot swallow another's work.

Migrate `VoteCog` and `AdminCog` onto it in the same commit, behaviour-identical. Note the
existing ordering coupling: `VoteCog` removes the reactor's reaction for anonymous sessions
before anything else runs (`squid/bot/voting/vote.py:73`), so its subscriber must keep
performing that removal itself rather than relying on dispatch order.

Commit: `bot: route raw reaction events through one dispatcher`.

## Step 3 — Persistence

`squid/starboard/infrastructure/models.py`, registered in `squid/persistence/__init__.py` (an
unregistered models module makes autogenerate emit *drops* — `docs/new-migration.md`), then one
Alembic revision after head `e1a7c3d9f5b2`.

```
starboards
  id                bigint identity primary key
  guild_id          bigint  -> server_settings.server_id  on delete cascade   # destination
  channel_id        bigint not null                                           # destination
  name              text not null
  enabled           bool not null default true
  required          double precision not null default 3      # weighted, so not an integer
  required_remove   double precision not null default 0
  self_vote, allow_bots, require_image                bool not null
  min_age_seconds, max_age_seconds                    int  not null default 0
  autoreact_upvote, autoreact_downvote                bool not null default true
  remove_invalid_reactions, link_edits, link_deletes  bool not null
  display_emoji text, colour int, jump_to_message bool, attachments_list bool,
  replied_to bool, ping_author bool
  created_at timestamptz not null default now()
  unique (guild_id, lower(name))
  check  (required > required_remove)

starboard_emojis
  starboard_id -> starboards.id on delete cascade
  emoji        text
  direction    text check (direction in ('up','down'))
  multiplier   double precision not null default 1.0  check (>0 and finite)
  position     smallint not null
  primary key (starboard_id, emoji)
  unique (starboard_id, position)

starboard_sources                       # destination/source split: the cross-guild hook
  starboard_id -> starboards.id on delete cascade
  guild_id     -> server_settings.server_id on delete cascade
  channel_id   bigint not null default 0     # 0 = whole guild, matching vote_session_options
  approved_by  bigint, approved_at timestamptz
  primary key (starboard_id, guild_id, channel_id)
  check (guild_id = <owning guild> or approved_at is not null)   -- enforced in the service

starboard_origin_messages
  id            bigint primary key            # the Discord message id
  guild_id      -> server_settings.server_id on delete cascade
  channel_id    bigint not null
  author_id     bigint not null
  author_is_bot bool not null
  is_nsfw       bool not null default false
  has_image     bool not null default false
  posted_at     timestamptz not null          # from the snowflake; drives age rules + analytics
  seen_at       timestamptz not null default now()

starboard_votes
  starboard_id      -> starboards.id on delete cascade
  origin_message_id -> starboard_origin_messages.id on delete cascade
  user_id           bigint
  emoji             text not null
  direction         text check (direction in ('up','down'))
  weight            double precision not null check (>0 and finite)
  target_author_id  bigint not null           # denormalised; leaderboards join nothing
  created_at        timestamptz not null default now()
  primary key (starboard_id, origin_message_id, user_id)
  index (starboard_id, target_author_id, created_at)
  index (origin_message_id)

starboard_entries
  starboard_id      -> starboards.id on delete cascade
  origin_message_id -> starboard_origin_messages.id on delete cascade
  posted_message_id bigint, posted_channel_id bigint
  score             double precision not null default 0
  raw_count         int not null default 0
  last_rendered_score double precision        # lets a refresh skip a no-op edit
  first_posted_at   timestamptz
  updated_at        timestamptz
  primary key (starboard_id, origin_message_id)
  unique (posted_message_id) where posted_message_id is not null
  index (starboard_id, score desc)
```

Two schema choices worth defending:

**Origin messages get their own table rather than reusing `messages`.** `messages`
(`squid/messages/infrastructure/models.py`) is keyed by message id with a single-valued
`purpose` column, so a build's `build_original_message` row and a starboard origin row for the
same message would collide. It also has FKs to `builds` and `vote_sessions` that mean nothing
here.

**One vote row per (starboard, message, user), not per emoji.** This matches Starboard-4's
cardinality and, crucially, stops a user doubling their weight by reacting with both ⭐ and 🌟.
The removal rule is the subtle part and must be written down: *removing a reaction deletes the
vote only when the removed emoji equals the stored emoji*; removing a different emoji of the
same direction is a no-op. A reaction in the opposite direction overwrites the row.

Commit: `starboard: add persistence schema`. Then `just db-upgrade && just db-check` and confirm
`alembic heads` is still single.

## Step 4 — Domain rules

`squid/starboard/domain/models.py` holds frozen snapshot dataclasses (`StarboardConfig`,
`StarboardEmoji`, `StarboardSource`, `OriginMessage`, `StarboardVote`, `StarboardEntry`) and two
**pure functions** that carry all the interesting logic:

```python
def evaluate_vote(config, origin, actor, emoji) -> VoteVerdict     # ~ Starboard-4 vote_status.rs
def decide_entry_action(config, entry, score, origin_present) -> EntryAction   # ~ msg_status.rs
```

`VoteVerdict` is `accept(direction)` | `ignore` | `remove_reaction`, covering self-vote,
bot-author, require-image, and the min/max age window. `EntryAction` is `SEND | UPDATE | REMOVE |
NOOP`.

The hysteresis rule, stated once so it can be property-tested: `score <= required_remove` →
`REMOVE`; `score >= required` → `SEND` (or `UPDATE` if already posted); strictly between →
`UPDATE` if posted, `NOOP` if not. The invariant worth a hypothesis test is *no flapping*: a
monotonically increasing score never produces a `REMOVE` after a `SEND`, and no score value
produces both for the same entry state.

Commit: `starboard: add scoring and posting rules`, with `tests/unit/starboard/` alongside.

## Step 5 — Application service

`StarboardService` (`squid/starboard/application/services.py`) over a `StarboardRepository` port,
mirroring how `VoteService` sits over `VoteRepository`.

- `record_vote(origin, actor, emoji)` — resolve which starboards claim the origin's guild/channel
  via `starboard_sources`, run `evaluate_vote` per config, compute each weight through the shared
  `RoleWeightPolicy`, upsert the vote rows, recompute scores, and return the per-starboard plan.
- `withdraw_vote(...)`, `clear_votes(origin_message_id, emoji=None)`.
- `refresh(origin_message_id, *, force=False) -> Sequence[EntryPlan]` — recompute and return
  what the transport must do. **The service never touches Discord**; it returns plans and the cog
  executes them. That is what keeps `squid.starboard.application` clean under the archrule at
  `tests/architecture/test_boundaries.py` and makes threshold behaviour testable with no bot.
- `recount(origin, reactions)` — takes reactor lists the transport already fetched.
- Config CRUD: `create_starboard`, `delete_starboard`, `list_for_guild`, `update_settings`,
  `set_emojis`, `add_source`, `set_role_multiplier`.

**Concurrency.** Every score-mutating path runs in one transaction that first takes
`pg_advisory_xact_lock(hashtextextended('starboard', origin_message_id))`. `SELECT … FOR UPDATE`
is not enough here because the entry row frequently does not exist yet — this is the analogue of
Starboard-4's in-process `post_update_lock`, but it holds across processes, which will matter the
moment cross-guild broadcasting means more than one shard.

Wire into `squid/runtime.py` (`ApplicationServices.starboards`) and `squid/bootstrap.py`.

Commits: `starboard: add the starboard application service` and
`starboard: add the starboard repository`, with `tests/integration/starboard/infrastructure/`
following `tests/integration/voting/infrastructure/`.

## Step 6 — Discord transport

`squid/bot/starboard/` — `cog.py`, `render.py`, `debounce.py`.

**Rendering** (`render.py`) uses Components V2 via `squid/bot/utils/components.py`: a
`StaticLayout` containing a `Container` with a `Section` (author name + avatar `Thumbnail`), the
message content, a `MediaGallery` of attachments when `attachments_list`, a link `Button` to the
original when `jump_to_message`, and a footer of `{display_emoji} {score:g} · #channel`. Note the
score is a float, so render it `:g` and show raw count alongside when they differ.

**Listeners** (all via the Step 2 router or direct cog listeners):

| Event | Behaviour |
|---|---|
| reaction add/remove | `record_vote` / `withdraw_vote`, then debounced refresh; delete the reaction when the verdict is `remove_reaction` and `remove_invalid_reactions` |
| reaction clear / clear_emoji | `clear_votes`, then refresh |
| `on_raw_message_edit` on an origin | refresh when `link_edits` |
| `on_raw_message_delete` on an origin | refresh when `link_deletes` (which resolves to `REMOVE`) |
| `on_raw_message_delete` on a *post* | null out `posted_message_id` so the entry can repost |
| `on_guild_channel_delete` | disable starboards whose destination vanished |

**Debounce** (`debounce.py`): a coalescing task keyed by `(starboard_id, origin_message_id)` that
waits ~2s and then re-reads the plan, so a burst of thirty stars produces one edit rather than
thirty. Combined with the `last_rendered_score` check this replaces Starboard-4's per-channel
edit cooldown and is strictly kinder to the rate limiter. On shutdown, `cog_unload` must drain
pending tasks.

Autoreact posts the configured emojis onto the new starboard post, spaced out, and must ignore
`discord.Forbidden`.

Commit: `starboard: post and refresh starred messages`.

## Step 7 — Commands

`/starboard create <channel> [name]`, `delete`, `list`, `show <name>`,
`edit <name> <setting> <value>`, `emoji add|remove|list`, `weight set|remove|list`,
`recount <message>`. Server-admin gated with `check_is_server_admin`
(`squid/bot/utils/permissions.py`), `hybrid_group`, `guild_only`, and every user-facing string
wrapped in `_()` / `app_commands.locale_str` per `docs/i18n.md`.

`recount` needs the advisory lock plus a per-guild cooldown; it pages through
`message.reactions[…].users()` and rebuilds vote rows through `StarboardService.recount`.

Commits: `starboard: add configuration commands` and `starboard: weight stars by role`.

## Testing

- **Unit** (`tests/unit/starboard/`): `evaluate_vote` and `decide_entry_action` exhaustively,
  including the hypothesis no-flapping property and weight monotonicity.
- **Integration** (`tests/integration/starboard/`): repository against real Postgres — concurrent
  `record_vote` on one message under the advisory lock, the removal-rule edge case above, cascade
  behaviour when a starboard is deleted.
- **Architecture**: the new context is covered automatically by the existing archrules; the cog
  is covered by the Components V2 rule (see Step 0).
- Per `CLAUDE.md`, run the focused set during development with `--no-cov`, then
  `just test`, `alembic heads`, and `git diff --check` at the end. Full integration to CI.

## Risks and open questions

- **Reaction volume.** Every reaction in every source channel now costs a config lookup. Cache
  the per-guild "is this a starboard emoji at all?" set in the service and check it before any
  I/O — Starboard-4 does exactly this (`StarboardConfig::is_guild_vote_emoji`) and it is the
  single most important optimisation in the feature.
- **Permissions.** `remove_invalid_reactions` needs Manage Messages in the *source* channel;
  posting needs Send Messages + Embed Links in the destination. Detect and report on
  `/starboard create` rather than failing silently later.
- **NSFW.** Never mirror an NSFW-channel message into a non-NSFW starboard. Check at post time,
  not vote time, since a channel's flag can change.
- **Cross-guild consent (deferred to phase 3, but decide the shape now).** A source guild's admin
  must run an approval command before `starboard_sources` accepts a foreign `guild_id`; the
  schema records `approved_by`/`approved_at`. Do not let v1 create such a row at all.
- **`required` as a float.** Weighted votes make integer thresholds wrong, but it means
  `required = 3` is satisfiable by one staff member at 3× — surprising unless documented. Ship
  the default role multiplier for starboards as 1.0 with no staff fallback, unlike voting.
