# 25 — Devtools as a library cog

## Problem

Plan 13 shipped the diagnostics substrate package-side (`sl.discord.mounts()` over the
weak live registry, `Mount.snapshot()` as the contract) but left the cog host-side in
`squid/bot/devtools.py` + `devtools_view.py`. A library user gets the data and no way to
look at it. Two commands the 2026-08-21 comparison flagged are also still missing: the
plan report and the planner metrics, both already carried by `Mount.snapshot()`.

## Design

> The cog is a viewer over contracts that already exist; it owns no state and no policy.

1. **`squid_layouts.discord.devtools.DevTools(check=..., registry=None)`** — a
   `commands.Cog` the host adds explicitly (`await bot.add_cog(...)`). No auto-setup, no
   app commands: prefix-only and hidden, exactly as plan 13 argued (a tree sync would
   show them to everyone).
2. **Authorization is injected.** `check` is a `Callable[[Context], Awaitable[bool]]`
   defaulting to owner-only. One `cog_check` gate, so a new subcommand cannot forget it —
   the plan-13 pattern, kept.
3. **Data sources**: live mounts from `sl.discord.mounts()` (plan 13's registry — this
   cog does *not* require plan 24's `MountRegistry`; passing one merely labels sessions
   with their keys). Routers via a new public `sl.discord.routers(client)` accessor over
   the `_INSTALLED` weak registry that the double-dispatch hardening added — the cog must
   not read a private dict, and hosts get the accessor for free.
4. **Commands**: `ui list`, `ui inspect <mount>`, `ui scene <mount>` move as-is;
   new `ui plan <mount>` (the retained `PlanReport`, events grouped by severity) and
   `ui metrics <mount>` (`states_explored`, `search_fallback`, cache disposition);
   `routes` renders `Router.describe()` for every router on the client.
5. **`devtools_view.py` moves too.** It renders with squid_layouts itself — the library's
   own inspector is authored in the library's semantic vocabulary, which is both the
   dogfood and the demo.

## Consumers

`squid/bot/devtools.py` shrinks to `add_cog(sl.discord.devtools.DevTools(...))` with the
bot's dev-mode gate as `check`.
