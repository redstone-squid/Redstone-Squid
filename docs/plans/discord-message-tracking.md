# Discord message tracking

## The problem this replaced

Six mechanisms tracked Discord messages, and they overlapped badly.

`messages` was keyed by the Discord snowflake but carried a single `purpose` and a
single owner, so one message could serve exactly one use. Every use added after the
first had to invent its own storage. That is how the codebase ended up with a
projection state machine on the same row, a coalescing queue with a per-resource
generation counter, raw snowflake columns on `delete_log_vote_sessions`, a fully
parallel scheme in `starboard_entries`, and three in-memory structures per vote
session kept in step by hand.

Three of those had each grown a private answer to "does a post already exist here?":
an `already_posted` channel set in the confirmed-build handler, a blake2b nonce in
the review session, and a `posted_message_id` column on the starboard entry. None of
them agreed on what to do when a post went missing.

It also lost data. `docs/plans/backfill-message-inference.md` recorded that a
build-log bundle could only be tracked to its first inferred build, and inference was
concatenating every message's text into one row's content.

And it had a live bug: acknowledging a sync job deletes the queue row, so the
generation counter restarted at 1, and projecting that 1 onto a message already
acknowledged at a higher generation violated a check constraint — inside the
statement that enqueued the work, so it aborted the user's build edit.

## The shape now

**One fact table.** `messages` holds one row per Discord snowflake carrying only what
is true about the message: channel, guild (nullable, so DMs are representable),
author, content, and the timestamps for when Discord made it, when the bot first saw
it, when its content was last refreshed, and when it was reported gone. No purpose,
no owner, no projection state. Deletion tombstones rather than erases; a message row
is a retained fact.

`content` is deliberately never exposed through the API — see
`docs/plans/rest-api.md`, enforced by `tests/architecture/test_message_content_privacy.py`.
It is retained for offline build inference, the edit context menu, and rendering a
delete-log card without refetching.

**Links say why a message matters.** Each points at `messages` with `RESTRICT`,
because a fact outlives its uses, and at its domain row with `CASCADE`, so deleting
the domain object drops only the link. That asymmetry is what removed the orphan rows
the old `ON DELETE SET NULL` left behind.

| Link | What it means |
|---|---|
| `discord_posts` | The bot owns this message and renders a resource into it |
| `build_source_messages` | A build was submitted or inferred from this message |

`build_source_messages` is many-to-many in both directions, which is what fixed the
backfill data loss: one message can source a whole bundle, and one build can span a
body message plus follow-up images.

**One reconcile loop.** `discord_posts` holds only applied state; what a post *should*
look like lives on the `discord_sync_queue` row, so staleness is a join rather than a
desired revision copied onto every post by a trigger. A partial unique index over
`(resource_kind, resource_key, channel_id)` makes "one live post per resource per
channel" a database guarantee, replacing all three hand-rolled idempotency schemes.

A `PostRenderer` answers only *which channels should hold a post for this resource,
and what does it say*. Sending, editing, deleting and recording belong to
`PostReconciler`, so a renderer never has to be idempotent or know whether it has run
before. Deletion needs no special case: a vanished resource reports wanting no posts.

| Renderer | resource_kind | resource_key |
|---|---|---|
| `BuildCardRenderer` | `build` | build id |
| `VoteSessionRenderer` | `vote_session` | session id |
| `StarboardEntryRenderer` | `starboard_entry` | `{starboard_id}:{origin_message_id}` |

**Deletion policy is one declared field.** The surfaces genuinely disagree, so
`repost_if_deleted` states it. A build card stays deleted because a moderator meant
it; a starboard post is a mirror of something else, so it comes back.

**Generations come from a sequence.** `discord_sync_generation_seq` is global and
exempt from rollback, so a generation can never name a revision below one already
applied. This is what the per-resource counter got wrong.

## Rules worth keeping

- **Reads do not write.** `get_or_fetch_message` used to delete a message's tracking
  row on a 404, which made a lookup quietly destroy state and forced four of its eight
  callers to opt out. Deletions are recorded by the raw delete event and by the
  reconciler when it finds a post missing. The reconcile loop is the only place a read
  still writes, and finding a post gone is the entire point of looking there.
- **Publication is explicit where the location is a human decision.** A build review
  belongs in each guild's configured vote channel, so the renderer fills any that are
  missing. A delete-log vote or a published poll goes where the author ran the command,
  so the command sends the message and hands it to `PostReconciler.adopt`.
- **Nudges are for latency, never correctness.** `bot.refresh_posts` and the starboard
  `EntryDebouncer` both run the same diff loop the background job drains. The write
  that prompted them already enqueued durable work, so a dropped nudge costs seconds
  rather than a missing post.
- **Triggers enqueue; application code does not.** Per `docs/plans/rest-api.md`, the
  API needs zero knowledge that Discord exists. A build change also enqueues its vote
  sessions, because a review card embeds the build it is voting on.

## Still open

- `VoteTarget` conflates a build target and a message target, and the voting service is
  still addressed by `message_id` rather than session id. `get_by_message` was
  repointed at `discord_posts` rather than rewriting the addressing model; see
  `docs/plans/pr-183-review/09-voting-redesign.md`.
- `Base.__init_subclass__` extracts attribute docstrings intending to become column
  comments, but the columns are not resolvable at that point, so it silently does
  nothing. Only explicit `comment=` kwargs work — three columns across the whole schema.
