# Reconcile Discord-derived state

> **Status (2026-11-07).** Draft. Nothing below has started. Split out of
> `docs/plans/schema-audit.md` Phase 2, where a foreign key on `votes.guild_id` turned out to depend
> on `server_settings` rows that the bot may never have created.

## Problem

The bot learns about Discord almost entirely from gateway events: a guild was joined, a message was
edited, a reaction was added, a channel was deleted. Any of those can be missed. The bot may be
offline, a reconnect may fall outside Discord's resume window, or a worker may crash mid-handler.
When an event is missed, the stored state is wrong and, for most kinds of state, nothing ever
repairs it.

Some parts of the codebase already handle this well, and they are the model to copy:

- **Outbound posts** are reconciled. `discord_sync_queue` and `PostReconciler`
  (`squid/bot/posts/reconciler.py`) converge Discord to the database and treat `NotFound` as
  "the post is gone" (see `docs/plans/discord-message-tracking.md`).
- **Vote reactions** are reconciled. `VoteReactionHandler.reconcile_open_reactions`
  (`squid/bot/voting/reactions.py`) runs every 60 seconds and rebuilds ballots from the reactions
  actually on each open card.

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
2. **A reconciler** reads Discord's current state and makes the database match it. It must be
   idempotent and safe to run at any time. It runs on every `on_ready` (which fires after a fresh
   session, not after a resume) and, where cheap enough, periodically.

Code that writes a row depending on Discord-derived state makes sure that state exists, for example
with get-or-create, instead of assuming the event that would have created it arrived.

## Phase 1 — Guild presence

- Add a guild-presence reconciler that runs on `on_ready`. It compares `bot.guilds` with
  `server_settings`, creates missing rows, and sets `in_server` to match reality in both
  directions. It reuses `SettingsService.guild_joined` / `guild_removed`.
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
  configuration against each guild's current channels. Clear or disable the ones that are gone,
  using the same logic as `on_guild_channel_delete`.
- Handle `on_guild_channel_delete` for the `server_settings` channel ids too, not only for
  starboard.

## Phase 3 — Starboard catch-up

Starboard entries currently depend on seeing every reaction event. Give starboard a reconciler
modeled on `reconcile_open_reactions`: re-read the reactions on recently active origin messages and
update the entries.

## Phase 4 — Source message facts

`messages` rows for messages the bot did not post (build source messages) are refreshed only by
edit and delete events. Re-check a source message against Discord whenever something uses it
(for example rendering a build that cites it), and mark it deleted on `NotFound`, the same way
`PostReconciler` does for posts.

## Decisions to make before starting

- **Phase 3 window:** how far back starboard catch-up looks (for example origin messages from the
  last 7 days) and how often it runs. Re-reading every reaction on every message is too expensive.
- **Phase 4 trigger:** check on use only, or also run a periodic sweep over recently cited messages.
- **Rate limits:** whether all reconcilers share one budget for Discord requests at startup, so a
  restart in many guilds does not set off a burst of fetches.
