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

Node-vocabulary and typing cleanup, still pre-release:

- Every semantic node now inherits `Renderable`, which only the containers did before, and
  `LayoutNode` is exactly `Renderable` as a result. `Renderable` also gained an empty
  `__slots__`, so the `slots=True` on every node class finally takes effect.
- `is_layout_node` now answers the open question — `isinstance(value, Renderable)` — so a
  frontend's own `Renderable` is accepted as content. It previously tested membership of a
  fixed tuple and answered False for one, disagreeing with the `LayoutNode` type. The closed
  test is `is_builtin_layout_node`, which is what the Discord and HTML lowering use.
- `Component.render` is abstract. A component that does not describe a message can no longer
  be mounted, and an intermediate base that leaves `render` to its subclasses needs no
  annotation to say so. `MessageLimits` and `FormField.parse` are likewise abstract.
- `RenderResult` and `RenderNode` are gone. `RenderResult` was byte-identical to
  `DocumentLike`, which is the public name; `RenderNode` was a synonym for `LayoutNode`.
- `render()` returning a bare string is now refused. It used to fall into the sequence branch
  and be drawn as one node per character.
- `GuardLedger.read`/`.write` take a typed `GuardKey` from `GuardLedger.bucket`, so a value
  written as one type and read back as another is a type error. `approvals()` returns one.
- Target extensions carry an `ExtensionKind[PayloadT, ResourceT]` instead of a bare string,
  so an extension's payload is checked against the adapter that consumes it.
- `FormField.format` answers in `PrefillValue` rather than `object`. A stored value no
  control could be seeded with now yields no prefill instead of being passed through.

Late pre-release hardening, after the version was first aligned:

- Every deliberate failure now derives from a per-distribution error root:
  `squid_reactivity.ReactivityError`, `squid_ui.SquidUiError` (shared by the widgets and
  Discord packages), `squid_storage.StorageError`, and `squid_replication.ReplicationError`.
  Original stdlib bases are retained, so existing catches keep working.
- The `squid_ui` root now exports the whole `LayoutError` family alongside `SquidUiError`.
- `squid_reactivity.action_result_sink()` scopes a result-sink registration to a block.
- `squid_reactivity.internals` is the sanctioned (unstable) seam through which sibling
  distributions reach the reactive core.
