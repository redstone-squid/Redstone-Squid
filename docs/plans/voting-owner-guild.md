# Owner-guild vote weights

## The flaw this fixes

The Aug 2026 dynamic-voting work made vote weights per-guild configuration
(`guild_vote_role_weights(guild_id, kind, role_id, multiplier)`) but left the tally
session-wide. A build review is carded in every guild with a vote channel and all its
ballots sum into one net against one threshold, so:

1. Any guild hosting a card could set its own multipliers and decide the shared
   outcome. Nothing stopped a guild from giving a role a 100x multiplier.
2. A voter in several guilds re-weighted to whichever card they last reacted to,
   because a session keeps one ballot per account (`ON CONFLICT DO UPDATE`).
   Voting from your best-weighted guild was strictly better.
3. The `+3 / -3` threshold meant nothing stable, since its units depended on which
   guilds' tables the ballots happened to pass through.

## The rule

A Discord role id only means anything inside one guild, so a session shared across
guilds has to name an authority. Every session derives an **owner guild**, and every
ballot is weighed against that guild's role table using the voter's membership
*there*, whichever card they actually reacted to.

| Kind | Owner guild |
| --- | --- |
| `build` | `BotIdentityConfig.owner_server_id`, the network's own server |
| `delete_log` | `target_server_id`, the server holding the message under vote |
| `generic` | `generic_vote_sessions.guild_id`, the guild that created the poll |

Voting from another guild's card still works. A voter who is not in the owner guild
weighs the default, so participation is never blocked by membership — only
multipliers are.

Two consequences worth knowing:

- A guild's weight table now binds **only the sessions it owns**. Editing a
  multiplier no longer reaches sessions merely carded there, and outside the
  network's own server a `build` table binds nothing at all. `/settings voting
  weight-set` says so when that applies.
- Eligibility is *not* part of this. The delete-log capability check still runs
  through the policy even when no owner guild is designated; only the multipliers
  drop away. Do not "optimize" the no-owner path by skipping the policy — that
  reopens ineligible delete-log voting.

### Why the owner is derived, not stored

`vote_sessions` gains no `owner_guild_id` column. Build ownership comes from config
at read time, so moving the network's home server takes effect immediately instead
of needing a backfill — and a migration cannot read `BotIdentityConfig` anyway
(see `rbac.md:619`). The other two kinds already store the guild they answer to.

### The resolver contract

`VoteActorResolver.resolve` distinguishes two empty answers, and the difference is
load-bearing:

- **A `VoteActor` holding no roles or capabilities** means "definitely not a member".
  The refresh may drop that voter to the default weight.
- **`None`** means "could not answer" — an unreachable or invisible guild. The
  refresh keeps the cached weight rather than rewriting it from an answer we never
  got.

`DiscordRestActorResolver.member()` deliberately does *not* follow this: it gates
access, so an unreadable guild denies exactly like an absent member.

At cast time an unreachable owner guild lands the ballot at the default weight
rather than rejecting it, so a Discord outage cannot veto a vote. The next refresh
corrects it.

## Poll scope

`generic_vote_sessions.scope` is `guild` or `network`. A network poll is carded in
every configured vote channel like a build review; a guild poll stays where it was
created. Weights follow the owner guild either way, which is what makes publishing
a poll network-wide safe.

Constraints, in the database as well as the service: a network poll must name an
owning guild (something has to weigh its ballots), and its options must be unscoped
(`guild_id` wildcard `0`) or every other server's card would have nothing to react
to. Creating one needs `vote.poll.network_create`, granted to nobody by default.

Closing is asymmetric: the author may close a network poll from any guild it
reached, while anyone else must stand in the owning guild — which is where the
`vote.poll.close_any` capability admitting them was resolved.

## Why the three kinds stay

A build review is, structurally, a generic session with a bound effect and a
particular card. That abstraction is already the design, and it lives in the close
path rather than in the session type:

```
status -> 'closed'
  -> emit_domain_event trigger (squid/persistence/postgres_entities.sql)
  -> vote_session.closed  {kind, result}
  -> ApplyBuildVoteOutcomeHandler   (squid/worker/events.py)   -> builds.confirm/deny
  -> DeleteVotedMessageHandler      (squid/bot/events/handlers.py)
```

Handlers re-read the snapshot and dispatch on the *target type*, not the kind, so a
new kind of bound effect is a new side table plus a new handler. Merging the three
kinds into one row shape would mean rewriting the trigger, the threshold and kind
check constraints, `_load_target`, and the renderer's dispatch, to arrive at the
same extension point. The seam is the event, not the schema.

## Known wart, out of scope

`VoteRepository._close_row` hardcodes `result = CANCELLED`, so a poll closed by its
deadline never reports `APPROVED`/`DENIED`. Nothing reads it today — the rendering
computes a winner from the tallies and generic polls have no close-effect handler —
but a generic poll that ever gains a bound effect will need this fixed first.
