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
  computed values, resources, topics, shared models, and auditable actions.
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

Model retention simplification, still pre-release:

- Remove `ReactivePool`, `ReactiveFactory`, and `squid_reactivity.state_pool`, including their
  `squid_ui.runtime` re-exports. Share model instances directly or retain them in an
  application-owned dictionary for keyed lookup.
- `PersistentStatePool` keeps its public API and now owns construction, hydration, and
  retention directly. It still rejects factories that reuse one model across storage scopes.

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

Action layout, still pre-release:

- `action_controls` accepts toggles and forms, and `ControlGroup` entries now pass its runtime
  check as its signature already promised. Shown as buttons, the controls keep their order and
  share rows; each `ControlGroup` starts a new row. Links and routed controls used to move to a
  separate row after the actions. When actions fold into a picker, toggles, forms, links, and
  routed controls stay buttons.
- `DiscordPreviewRenderer` draws entity selects with their placeholder and current values.
- Source-backed widgets (`Browser`, `SourceRankedList`) label paging Previous and Next.
  They said Older and Newer whatever the list's order, so a newest-first list pointed the
  wrong way and a ranking or alphabetical list was labelled by time. `Browser` takes a
  `chronology` (`sl.sources.Chronology`) for lists sorted by time; it labels both paging and
  stepping between opened items.
- A browser leaves out item stepping when the page holds one item, and puts Back after the
  step buttons.
- `Wizard` takes `actions`, the hosting screen's controls to append to the wizard's last row,
  and `WizardReview.note`, shown above Finish. A form-opening Continue shares its row with Back.

Late pre-release hardening, after the version was first aligned:

- Every deliberate failure now derives from a per-distribution error root:
  `squid_reactivity.ReactivityError`, `squid_ui.SquidUiError` (shared by the widgets and
  Discord packages), `squid_storage.StorageError`, and `squid_replication.ReplicationError`.
  Original stdlib bases are retained, so existing catches keep working.
- The `squid_ui` root now exports the whole `LayoutError` family alongside `SquidUiError`.
- `squid_reactivity.action_result_sink()` scopes a result-sink registration to a block.
- `squid_reactivity.internals` is the sanctioned (unstable) seam through which sibling
  distributions reach the reactive core.
