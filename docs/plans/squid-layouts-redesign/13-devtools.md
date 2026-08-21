# 13 — Runtime devtools

## Problem

squid-layouts has excellent *planning* diagnostics (`PlanReport`, `PlanMetrics`,
fingerprints, conform audits) but nothing answers "show me all live UI sessions and why
this one is weird" while the bot runs. CascadeUI's DevToolsCog (inspector, session/state/
history/perf commands) is its clearest product-polish win, and the data squid would
surface is already collected — it just has nowhere to go.

## Design

Owner-only diagnostics cog, host-side (`squid/bot/devtools.py`), built on data the
framework already exposes plus one small hook:

1. **Framework hook**: a process-wide weak registry of live mounts
   (`sl.discord.mounts()` — weakrefs, no lifecycle ownership, no tasks). Mounts
   register on `bind`, vanish on GC/finish. This is the only package change.
2. **`/dev ui list`**: live mounts — id, component class, message link, generation,
   dirty flag, age, timeout remaining, ephemeral flag.
3. **`/dev ui inspect <id>`**: one mount — `export_state()` of the component tree
   (persist fields only), presentation session (cursors/strategies), last `PlanReport`
   events and `PlanMetrics` (cache hit, search states, latency), handler keys by
   generation. Rendered through the engine itself, naturally.
4. **`/dev ui scene <id>`**: attach the current `SceneDocument` JSON (via
   `sl.scene.Codec`) as a file — the scene protocol finally earns its keep as a debug
   artifact.
5. Gate behind the bot's existing owner-permission machinery; register the cog only
   when a debug setting is on.

Not in scope: action history, middleware, profiling exports — add on demand.

## Verification

- Unit tests for the weak registry (mounts appear on bind, disappear on finish/GC).
- Manual pass via the `run` skill: open the settings panel, `/dev ui list`, `inspect`,
  `scene`, confirm no reference leak after closing (list empties).
