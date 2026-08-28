# Squid UI changelog

User-visible changes to the Squid UI framework suite. All six distributions —
`squid-reactivity`, `squid-ui`, `squid-ui-widgets`, `squid-ui-discord`, `squid-storage`,
and `squid-replication` — release in lockstep at one version, so one entry covers the set.
The hosted Redstone Squid bot is not versioned here.

The format follows [Keep a Changelog](https://keepachangelog.com/); dates are added when a
version's `squid-ui-v*` tag is pushed.

## 0.1.0a1 (unreleased)

Initial public alpha of the suite.

- `squid-reactivity`: dependency-free transactional reactive state — transactions,
  computed values, resources, topics, shared state pools, and auditable actions.
- `squid-ui`: semantic components, limits-aware planning to explicit targets, exact
  primitives, the resolved-scene codec with published JSON Schema (scene protocol 1), and a
  first-class native HTML target.
- `squid-ui-widgets`: portable wizards, editors, menus, tabs, browsers, decisions, votes,
  ranked lists, rosters, and the state machines beneath them.
- `squid-ui-discord`: discord.py 2.7 rendering for Components V2 and classic messages,
  message roots, sessions, routing, role panels, devtools, and opt-in durability.
- `squid-storage`: versioned scoped stores (memory, SQLite, Postgres), durable session
  records, persistent reactive state pools, and a Postgres topic bridge.
- `squid-replication`: replicated-state containers over a reference backend, Loro, and an
  experimental pycrdt backend.

Late pre-release hardening, after the version was first aligned:

- Every deliberate failure now derives from a per-distribution error root:
  `squid_reactivity.ReactivityError`, `squid_ui.SquidUiError` (shared by the widgets and
  Discord packages), `squid_storage.StorageError`, and `squid_replication.ReplicationError`.
  Original stdlib bases are retained, so existing catches keep working.
- The `squid_ui` root now exports the whole `LayoutError` family alongside `SquidUiError`.
- `squid_reactivity.action_result_sink()` scopes a result-sink registration to a block.
- `squid_reactivity.internals` is the sanctioned (unstable) seam through which sibling
  distributions reach the reactive core.
