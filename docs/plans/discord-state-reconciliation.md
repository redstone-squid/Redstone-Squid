# Reconcile Discord-derived state

> **Status (2026-11-07).** Decisions made; nothing below has started. Split out of
> `docs/plans/schema-audit.md` Phase 2, where a foreign key on `votes.guild_id` turned out to depend
> on `server_settings` rows that the bot may never have created.

## Problem

The bot learns about Discord almost entirely from gateway events: a guild was joined, a message was
edited, a reaction was added, a channel was deleted. Any of those can be missed. The bot may be
offline, a reconnect may fall outside Discord's resume window, or a worker may crash mid-handler.
When an event is missed, the stored state is wrong and, for most kinds of state, nothing ever
repairs it.

Two parts of the codebase already repair themselves:

- **Outbound posts** are reconciled. `discord_sync_queue` and `PostReconciler`
  (`squid/bot/posts/reconciler.py`) converge Discord to the database and treat `NotFound` as
  "the post is gone" (see `docs/plans/discord-message-tracking.md`). This is the model to copy.
- **Vote reactions** are reconciled. `VoteReactionHandler.reconcile_open_reactions`
  (`squid/bot/voting/reactions.py`) runs every 60 seconds and rebuilds ballots from the reactions
  actually on each open card. It does so by fetching every open card and paging through each
  reaction's users, which breaks the request rule below. Phase 5 replaces it.

Inbound state elsewhere has only the event path:

| Stored state | Learned from | Repair if the event is missed |
|---|---|---|
| `server_settings` row and `in_server` | `on_guild_join` / `on_guild_remove` (`squid/bot/settings.py`) | None. A guild joined while offline has no row; one left while offline stays `in_server = true`. |
| `server_settings` channel ids (`fastest_channel_id`, …) | Admin commands | None. A deleted channel is never cleared. |
| Starboard sources and channels | `on_guild_channel_delete` (`squid/bot/starboard/cog.py`) | None. |
| Starboard entries | Raw reaction, edit and delete events | None. Reactions added while offline are never counted. |
| `messages` edits and deletes | `on_raw_message_edit` / `_delete` (`squid/bot/messages.py`) | Only for messages the bot itself posts, via `PostReconciler`. Source messages are not repaired. |

## Rule

Every piece of stored state derived from Discord has two paths:

1. **The event path** applies changes quickly while the bot is connected.
2. **A reconciler** makes the database match Discord's current state. It must be idempotent and
   safe to run at any time.

Code that writes a row depending on Discord-derived state makes sure that state exists, for example
with get-or-create, instead of assuming the event that would have created it arrived.

**Reconcilers never fan out Discord requests.** An automatic reconciler reads only what discord.py
already caches from the gateway: `bot.guilds` and each guild's channels arrive with `READY` and
`GUILD_CREATE` and cost no requests. Anything that needs an HTTP request per object (a message, its
reactions) is never swept. It is checked when something uses that object, or when a user asks
for it explicitly. A restart therefore never sets off a burst of fetches, and the reconcilers need
no shared rate-limit budget.

Not every miscount needs repairing. Where a missed event only makes a number slightly wrong
(starboard counts), an explicit, user-triggered repair is enough.

## Phase 1 — Guild presence

- Add a guild-presence reconciler that runs on `on_ready` (a fresh session, not a resume). It
  compares the cached `bot.guilds` with `server_settings`, creates missing rows, and sets
  `in_server` to match reality in both directions. It reuses `SettingsService.guild_joined` /
  `guild_removed`.
- Make casting a vote ensure the guild's settings row exists, using `get_or_create_for_update`'s
  insert-on-conflict (`squid/settings/infrastructure/repository.py`), in the same transaction as
  the vote.
- Then add the FK `votes.guild_id` → `server_settings.server_id` with `ON DELETE RESTRICT`,
  matching `generic_vote_sessions.guild_id`. This depends on `schema-audit.md` Phase 2 having made
  `votes.guild_id` nullable.
- Audit the other FKs to `server_settings` (`starboards`, `starboard_sources`,
  `starboard_origin_messages`, `messages.server_id`, `guild_vote_emojis`,
  `guild_vote_role_weights`, `generic_vote_sessions`) and apply the same ensure-on-write rule to
  each of their write paths.

## Phase 2 — Channels that no longer exist

- Extend the presence reconciler to check channel ids stored in `server_settings` and starboard
  configuration against each guild's cached channels. Clear or disable the ones that are gone,
  using the same logic as `on_guild_channel_delete`.
- Handle `on_guild_channel_delete` for the `server_settings` channel ids too, not only for
  starboard.

## Phase 3 — Starboard recount on request

**Decision:** no automatic starboard reconciliation. A reaction missed while the bot was offline
leaves a count slightly low, which is not worth a sweep. Follow
[Starboard-4](https://github.com/CircuitSacul/Starboard-4) (`src/core/starboard/recount.rs`,
`src/interactions/commands/chat/utils/recount.rs`):

- Add a `recount <message link>` command that re-reads the reactions on one origin message,
  rebuilds its starboard votes, and refreshes its entries.
- Allow one recount per message at a time (a per-message lock), and add a per-guild cooldown so
  the command cannot become a request burst.
- The reaction event path gets or creates the origin message row, as Starboard-4's
  `get_or_create_original` does, instead of assuming an earlier event recorded it.

## Phase 4 — Source messages, checked on use

**Decision:** check only when used, never in a sweep. `messages` rows for messages the bot did not
post (build source messages) are otherwise refreshed only by edit and delete events. Whenever
something uses a source message (for example rendering a build that cites it), fetch it, update
the stored content, and mark it deleted on `NotFound`, the same way `PostReconciler` does for
posts.

## Phase 5 — Ballots recount on demand and at the end

**Decision:** remove the periodic `vote-reaction-reconciliation` job (`VoteReactionHandler.start`,
`squid/bot/voting/reactions.py`). Ballots are recounted from Discord only when someone asks or when
the session is about to close.

- **On demand:** add a `recount` command for a vote card. It calls the existing, currently unused
  `reconcile_message_reactions`. As in Phase 3, allow one recount per session at a time (replacing
  the handler's single global `_lock` with a per-session lock) and add a per-guild cooldown.
- **At the end:** recount immediately before the session is finalized, so the result reflects the
  reactions actually on the cards:
  - *Manual close* (`squid/bot/voting/controls.py`): recount, then close.
  - *Deadline close* runs in the worker (`squid/worker/app.py`, `_close_due_votes`), which has no
    gateway connection. Move the recount logic out of the bot cog into the voting application
    layer, behind a port for reading a card's reactions. The bot implements it from discord.py and
    the worker implements it with the existing REST adapter (`squid/voting/infrastructure/discord_rest.py`).
    The worker then recounts each due session before closing it.
  - *Threshold close* (`_close_at_threshold`) happens inside the cast that crosses the threshold
    and does not recount. That cast is a live event, and recounting there would put Discord
    requests on the vote path. Anyone who suspects missed votes can recount before the threshold
    is reached.

A final recount is a bounded number of requests per session, made once per session, so it fits the
request rule above.
