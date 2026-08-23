# 13 — Runtime devtools

**Status: shipped.**

## Problem

squid-layouts had excellent *planning* diagnostics — `PlanReport`, `PlanMetrics`,
fingerprints, conform audits — and every one of them describes a render that already
happened. Nothing answered "show me all live UI sessions and why this one is weird" while
the bot runs. CascadeUI's DevToolsCog is its clearest product-polish win, and the data squid
would surface was already collected; it just had nowhere to go.

## Framework API (`squid_layouts/discord/`)

**`live.py` — a process-wide weak registry.** `sl.discord.mounts()` returns every live mount
in first-render order, and `sl.discord.live.find(id)` returns one. Entries are values in a
`WeakValueDictionary`, so the registry owns no lifetime, starts no task, and is on no hot
path. A mount registers in `_commit` — the point where a render becomes something a reader
can see and click — and deregisters through a `Mount.on_finish` hook (shipped in
[12](12-session-policy.md)) rather than waiting for the collector: a finished mount is still
referenced by whatever host object opened it, so listing it as live would be a lie that
outlives the session by minutes.

12's `MountRegistry` is *not* this registry. It holds only keyed and parented mounts, a
strict subset of what `/dev ui list` has to show.

**`Mount.snapshot() -> MountSnapshot`.** One call rather than a dozen new properties: it
fixes what a mount will say about itself, and a caller cannot mutate what it reads. It
carries the id, the root component's qualified name, `MountAddress`, generation, dirty and
finished flags, age, idle, remaining timeout, `lock_to`, the live generation's handler keys,
and the committed `SceneDocument`, `PlanReport` and `PlanMetrics`. Everything in it is a
scalar or already immutable, so nothing is copied and listing every session is cheap.

Three supporting additions made that possible, each small:

- **`Mount.plan`** keeps the `PlanResult` behind the generation on screen. Before this the
  mount dropped its composition at commit, so the only way to see the live scene was to
  stage a fresh render — which is a different scene, and which mutates `_pending`.
- **`Mount.address`**, a frozen `MountAddress` (message, channel, guild, jump URL,
  ephemeral). Deliberately *not* a `Message`: plan 07's rule is that a mount holds a way to
  write to its message, not the message. These are only coordinates, so they stay true after
  every handle has gone stale. Set from the delivered message in `send`, and from
  `interaction.message` on the first click — which is how a mount sent through an unwaited
  interaction response ever learns where it lives.
- **`_born`/`_active` monotonic marks.** `_active` moves on every commit and every dispatch,
  which is what discord.py's view timer actually restarts on, so `expires_in` is honest
  without reaching into a name-mangled attribute of a view the mount is about to replace.

`squid_layouts.runtime` now re-exports `export_state`/`restore_state`, which durability
already imported from the private module.

## Host API (`squid/bot/devtools.py`, `squid/bot/devtools_view.py`)

Prefix commands, not hybrids: they answer questions about *this process*, they take a mount
id nobody can guess, and putting them in the application command tree would cost a sync and
show them to everyone.

| Command | What it does |
|---|---|
| `!dev ui list` | Opens the inspector on every live mount |
| `!dev ui inspect <id>` | Opens the inspector focused on one |
| `!dev ui scene <id>` | Attaches the committed `SceneDocument` as protocol JSON |

`MountInspector` is itself a mounted component, so the engine renders its own diagnostics.
The list gives id, component, generation, flags, age, idle, remaining timeout and a jump
link, paged eight at a time, with a picker over the newest 25. The detail view adds the
message, the lock, the handler keys at the live generation, the plan (metrics, fingerprints,
adaptation events), the components' declared persistent state by path, and the presentation
session. Only the state dump paginates — two pagers in one message means two nav rows.

Decisions worth keeping:

- **It reads the world on every render, not once at construction.** A panel left open keeps
  telling the truth: sessions that ended while it was open are simply gone from the next
  render, and a focused mount that finished falls back to the list with a warning rather
  than showing a frozen dump.
- **Refresh is a state change.** A handler that mutates nothing leaves the mount clean, and
  `flush` then only defers — so re-reading the world had to bump a `revision` field or the
  message would keep showing the old dump.
- **The inspector appears in its own list, labelled.** The cog sets `inspector.own_id` after
  `create_mount`; unlabelled it reads as one unexplained session in the table it just drew.
- **Every reply goes out through `Private`.** A state dump is internal detail in the same
  class as a traceback: ephemeral on the slash side, DM on the prefix side, never a channel.
- **Untranslated, unlike the rest of `squid.bot`.** Owner-only, and most of what it prints
  is Python identifiers, state field names and planner event codes.
- **State is exported per component, inside a `try`.** `export_state` deep-copies; one field
  holding something that refuses to be copied must not cost the whole dump.

## Gating

`DEVELOPMENT_EXTENSIONS = ("jishaku", "squid.bot.devtools")` in `squid/bot/app.py`, loaded
on top of `EXTENSIONS` only in development mode. Owner-gated either way, via `cog_check` —
*not* a check on the group, because `invoke_without_command=True` skips the group's own
`prepare`, and with it the group's checks, whenever a subcommand is what ran. Keeping the
cog off a production process is the second lock: a mount id in a log line is then not one
command away from a dump of that session's state.

Not in scope, add on demand: action history, middleware, profiling exports.

## Verification

```
uv run pytest packages/squid-layouts/tests/test_live_mounts.py \
              packages/squid-layouts/tests/test_mount.py \
              tests/unit/bot/test_devtools_panel.py tests/unit/bot/test_devtools_cog.py \
              tests/unit/bot/test_extension_loading.py --no-cov
just typecheck
```

`test_extension_loading` now loads the development-only cogs too (minus third-party
`jishaku`): a dev cog can still collide with a production command name, and would do so on
the developer's machine rather than in CI.

Four package test doubles built their own message namespaces inline; they now use
`fake_message()`, which gained the channel, guild and jump-url fields an address needs.
