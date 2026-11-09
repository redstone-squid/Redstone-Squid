# Reconcile Discord-derived state

> **Status (2026-11-08).** Done. Split out of `docs/plans/completed/schema-audit.md` Phase 2,
> where a foreign key on `votes.guild_id` turned out to depend on `server_settings` rows that the
> bot may never have created. The sections below describe what was built; "Changed from the
> original plan" at the end lists where it departs from the first draft.

## Problem

The bot learns about Discord almost entirely from gateway events: a guild was joined, a message was
edited, a reaction was added, a channel was deleted. Any of those can be missed. The bot may be
offline, a reconnect may fall outside Discord's resume window, or a worker may crash mid-handler.
When an event was missed, the stored state was wrong and, for most kinds of state, nothing repaired
it.

Outbound posts already repaired themselves: `discord_sync_queue` and `PostReconciler`
(`squid/bot/posts/reconciler.py`) converge Discord to the database and treat `NotFound` as "the
post is gone" (see `docs/plans/completed/discord-message-tracking.md`).

| Stored state | Learned from | Repair now |
|---|---|---|
| `server_settings` row and `in_server` | `on_guild_join` / `on_guild_remove` | Presence sync on every fresh gateway session; rows created by any write that needs one (Phase 1). |
| `server_settings` channel ids | Admin commands; channel and thread deletion | Compared with the gateway cache when a guild becomes available (Phase 2). |
| Starboard destinations and sources | Channel deletion | Same as above (Phase 2). |
| Starboard votes | Raw reaction, edit and delete events | `/recount` on request (Phase 3). |
| `messages` content and deletion for messages the bot did not post | Raw edit and delete events | Checked when the bot uses the message (Phase 4). |
| Vote ballots | Raw reaction events | `/recount` on request, and before a poll closes (Phase 5). |

## Rule

Every piece of stored state derived from Discord has two paths:

1. **The event path** applies changes quickly while the bot is connected.
2. **A repair** makes the database match Discord's current state. It must be idempotent and safe
   to run at any time.

Code that writes a row depending on Discord-derived state makes sure that state exists instead of
assuming the event that would have created it arrived. For guilds this is
`ensure_server_settings` (`squid/settings/infrastructure/repository.py`), called by every
repository write that stores a guild ID referencing `server_settings`.

**Automatic repairs never fan out Discord requests per message, reaction or user.** They read what
discord.py already caches from the gateway: `bot.guilds` and each guild's channels arrive with
`READY` and `GUILD_CREATE` at no cost. The one allowed request is confirming that a single stale
reference is really gone (see Phase 2), which costs one request per stale reference and clears it.
Anything that needs requests per message or per user is checked when something uses the object,
when a user asks, or once at a natural end point such as a poll closing. A restart therefore never
sets off a burst of fetches.

Not every miscount needs automatic repair. Where a missed event only makes a number slightly wrong
(starboard counts, ballots of an open vote), a user-triggered repair is enough.

## Phase 1 — Guild settings rows and presence

- `ensure_server_settings(session, guild_ids)` inserts missing `server_settings` rows in the
  caller's transaction (`INSERT … ON CONFLICT DO NOTHING`). It is called from every write path of
  the tables that reference `server_settings`: stored messages (`MessageRepository.upsert_snapshot` and
  build source messages), generic polls, guild vote emojis and role weights, votes, starboards and
  starboard origin messages. `SettingsRepository.get_or_create_for_update` uses it too.
- `votes.guild_id` has a foreign key to `server_settings.server_id` with `ON DELETE RESTRICT`
  (migration `b8e3d6f9a2c4`, which first creates rows for guilds only votes mention).
- `SettingsService.sync_presence` makes `in_server` true for exactly the given guilds. The bot calls
  it from `on_ready` with `bot.guilds` (`GuildStateCog`, `squid/bot/guild_state.py`). discord.py
  dispatches `on_ready` for each fresh session and `on_resumed` for a resume, so a resume does not
  repeat it. `bot.guilds` includes guilds that are temporarily unavailable, so an outage does not
  mark them absent.

## Phase 2 — Channels that no longer exist

- `GuildStateCog` owns `on_guild_join`, `on_guild_remove`, `on_guild_channel_delete` and
  `on_raw_thread_delete`. Channel and thread deletion clear matching `server_settings` channels,
  disable boards that post there and remove the channel as a starboard source.
- `on_guild_available` compares the channels stored for the guild
  (`SettingsService.referenced_channels`, `StarboardService.referenced_channels`) with its cached
  channels. discord.py fires it for every guild at startup, after an outage, and the bot also runs
  it on joining (settings survive leaving a guild). An unavailable guild is skipped because its
  channels are not cached.
- A stored channel missing from the cache is fetched once before it is cleared, because an
  archived thread is missing from the cache too, and settings may name threads. Only a 404 clears
  it; 403 and other errors keep it. Disabled boards are not listed as references, so a deleted
  destination is not fetched again on every start.

## Phase 3 — Starboard recount on request

Following [Starboard-4](https://github.com/CircuitSacul/Starboard-4) (`src/core/starboard/recount.rs`),
there is no automatic starboard repair.

- `/recount <message link>` (`squid/bot/recount.py`) accepts a starboard post or the source message
  it mirrors. It needs `starboard.board.recount`. `StarboardCog.recount` reads every user of every
  starboard emoji on the source and replaces the stored votes through the existing
  `StarboardService.recount`, then refreshes the entries.
- One recount per source message runs at a time, and each guild may start one recount every 30
  seconds (shared with vote recounts).
- The reaction event path already got or created the origin row (`record_votes` upserts it); it
  now also ensures the guild's settings row.

## Phase 4 — Stored messages, checked on use

`RedstoneSquid.lookup_message` fetches a message and tells "gone" (404 for the message or its
channel) from "unreadable" (403). `RedstoneSquid.fetch_stored_message` uses it to refresh the stored
content (`MessageService.refresh_content`, which changes only differing rows) or mark the row
deleted. Uses wired to it:

- The deletion vote card renders its target through `fetch_stored_message`, and shows the stored
  content when the target is gone.
- The starboard renderer marks an origin deleted when Discord says it is gone.

Build source messages are not fetched anywhere today (`BuildHandler.get_original_message` has no
callers), so nothing else needed wiring. New code that fetches a stored message should use
`fetch_stored_message`.

## Phase 5 — Ballots recount on request and before closing

The periodic `vote-reaction-reconciliation` job is gone. The recount moved into the voting
application layer as `BallotRecounter` (`squid/voting/application/recount.py`), behind a
`VoteCardReactions` port. The bot implements it with its gateway client (`DiscordVoteCards`,
`squid/bot/voting/reactions.py`) and the worker with the REST API (`DiscordRestVoteCards`,
`squid/voting/infrastructure/discord_rest.py`). Member facts come from the existing
`VoteActorResolver` (the vote cog in the bot, `DiscordRestActorResolver` in the worker).

- **On request:** `/recount` with a vote card link, open to anyone, with the same per-session and
  per-guild limits as Phase 3.
- **Manual close** (`squid/bot/voting/controls.py`) defers the interaction, recounts, then closes.
- **Deadline close:** `VoteService.close_due(recount=…)` recounts each due poll before closing it.
  The worker passes its REST recounter. A failed recount is logged and the poll closes anyway, so a
  Discord outage cannot keep a poll open.
- **Threshold close** happens inside the cast that crosses the threshold and does not recount;
  that cast is a live event.

In the bot, live reactions and recounts for the same session take turns on a per-session lock
(`KeyedLock`, `squid/core/locks.py`), so a slow recount no longer blocks every other session.
Recounts cast with `toggle=False`, which selects an option instead of toggling it. A recount in
the worker racing a live reaction in the bot therefore cannot withdraw a ballot. It can still
replace a ballot cast between its read and its write with an older reaction; that window exists
only at the deadline and is accepted.

A recount never withdraws a ballot. A missing reaction does not show that a voter changed their
mind: the card may be unreachable, its reactions cleared, or an anonymous poll may have removed the
reaction after storing the ballot. If Discord cannot confirm a voter's membership, the recount
leaves that voter's reactions in place, since in an anonymous poll they are the only record.

## Changed from the original plan

- **Ensure-on-write lives in the repositories, not in each service.** The foreign key is a storage
  rule, so one helper at the storage layer covers every caller, including future ones. It also
  fixed a live bug the plan did not list: stored messages and polls in a guild joined while offline
  failed their existing foreign keys.
- **Presence and channel repair are one cog.** `GuildStateCog` replaces the guild listeners in
  `SettingsCog` and the channel listener in `StarboardCog`. Channel checks run on
  `on_guild_available` rather than inside the `on_ready` sweep, because discord.py already fires it
  per guild at the moments the cache becomes trustworthy.
- **The request rule allows one confirmation fetch per stale channel.** The cache cannot tell a
  deleted channel from an archived thread, and settings may name threads.
- **Deleted source channels are removed from starboards.** The original handler only disabled
  boards whose destination was deleted.
- **One `/recount` command serves votes and starboards.** The bot already has Discord's maximum of
  five message context menus, so it is a slash command taking a message link.
- **The recount uses select semantics** (`cast_vote(toggle=False)`), and the vote recount is open to
  anyone rather than gated by a permission: it only makes ballots match visible reactions.
- **Phase 4 targets the uses that exist.** Build source messages are never fetched, so the check is
  wired to deletion vote targets and starboard origins instead.
