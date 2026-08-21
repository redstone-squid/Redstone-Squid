# squid-layouts architecture and API interactions

squid-layouts separates UI intent, target planning, and drawing. Discord is one frontend,
not the data model: the same resolved scene can be drawn as Discord Components V2 or safe
HTML, serialized to JSON, and handed to another process.

## End-to-end flow

    Component state
        |
        | render()
        v
    semantic Document -- keyed components, assets, Discord Markdown text
        |
        v
    target adapters -- finite lossless strategies plus sticky presentation state
        |
        v
    exact primitives -- measured solve, declared degradation, pagination
        |
        +-- PlanReport (notes and fingerprints)
        +-- PlanMetrics (search/cache/latency instrumentation)
        +-- ephemeral ActionBindings (never serialized)
        v
    immutable SceneDocument -- `sl.scene.Codec` JSON and JSON Schema
        |
        +-- sl.discord.Renderer --> discord.ui.LayoutView
        +-- sl.html.Renderer ----> safe semantic HTML

The planner is the only layer allowed to choose an alternate, drop content, split a page, or
spend a target resource. A renderer is mechanical. If Discord drawing needs to clamp after
planning, that is a DrawInvariantError, not a second degradation mechanism.

## Which entry point to use

| Need | API | Result |
|---|---|---|
| Stateful Discord interaction | sl.discord.Mount(component) | lifecycle, events, paging, edits |
| Static Discord message | sl.discord.render_static(document) | discord.ui.LayoutView |
| Discord view plus diagnostics | sl.discord.compose(document) | Composition |
| Portable planning | plan(document, target=...) | PlanResult |
| Browser or preview drawing | sl.html.Renderer().draw(scene) | HTML string |
| Cross-process transport | sl.scene.Codec.dumps and loads | canonical protocol JSON |
| Resume an opted-in session | sl.discord.durability registry and `MountManager` | restored Mount |

sl.discord.compose is the Discord convenience path: plan for sl.discord.Target, draw with
sl.discord.Renderer, then strictly audit the result. Detached composition can pass
reserved_text; composing the complete document is preferable because the planner can see
every cost. It never adopts an arbitrary existing `discord.py` view: renderers own their
output object, so unknown pre-existing controls cannot undermine measurement.

## Semantic authoring, adaptation, and exact primitives

The package root is semantic-first. Structural nodes are `Group`, `Stack`, `Cluster`,
`Section`, `Article`, and `Aside`; content includes `Heading`, `Paragraph`, `List`, `Fields`,
`Table`, `Quote`, `Code`, `Media`, `Details`, and measures; interactions are `Actions`,
`Choices`, `Items`, and `Navigation`. These say what the information means and preserve
stable string keys, not which Discord widget must appear.

Author them through the lowercase factories — `sl.section(*children, heading=...)`,
`sl.actions(*entries, key=...)`, `sl.action(label, handler, key=...)`. Content is positional,
identity and configuration are keyword-only, `None`/`False` children are skipped so
`cond and node` composes, and bare strings or t-strings in a child position become a
`Paragraph`. Collections are unpacked by the caller. The dataclasses remain the IR and remain
public; the factories only normalize what authors write.

Adapters choose among finite lossless strategies. Actions may be individual controls,
grouped pickers, or a paged picker. Thirty-six ungrouped actions become 25 and 11 options;
author-declared groups never merge. Choices, Items, and Navigation use keyed 25-option
windows. Cross-page multi-selection is rejected because a page-local Discord select cannot
honestly express that domain operation without an explicit grouping or commit model.

Strategy ranking is lexicographic rather than scalar: representation stability by
`Flexibility`, author display preference, pager count, transition distance, then stable path
and strategy identifiers. Per-adapter versions invalidate only that adapter's sticky state.
The default search budget is 512 states. Budget exhaustion selects a deterministic lossless
fallback and records `planner.search_fallback`; it never spends an author degradation grant.

Target-shaped nodes live under `squid_layouts.primitives`. Their policies are explicit:

- `Truncate` and `Spill` shorten content only when the author wraps or configures it.
- `Alt`/`Alts` supply text ladders and per-entry drop priority.
- `Paginate` has an explicit key and measured footer/navigation chrome.
- `Variants` supplies an ordered ladder of complete structural alternates for component
  pressure; rungs may be capability-gated, and the planner filters them before the solver
  steps the survivors.
- `Drop` and `Never` make omission or non-degradation explicit.

Semantic helpers `truncate`, `spill`, `optional`, `fallback`, and `best_effort` grant the
same losses at intent level. Consequential actions, status, and code are never silently lost.

Target-native features use Extension(kind, version, payload, fallback). A target adapter
prepares and measures the native resource once. Unsupported targets use the mandatory
portable fallback. Extension payloads in scenes are versioned and JSON-safe.

Discord Markdown is the default text dialect, not a structured inline-content tree. Bare
strings are trusted author markup. `md(t"Build {title}")` safely escapes Python 3.14 template
interpolations and neutralizes mentions; `plain()` requests literal text; `raw_md()` opts one
known-safe interpolation back into trusted markup. Scenes preserve the dialect so every
renderer can choose an appropriate Markdown implementation.

## Components and Vue-inspired reactivity

Components render synchronously from state. state observes assignment and nested list, dict,
and set mutation. A default is deep-copied per instance, so `sl.state([])` is safe; reach for
`state(factory=...)` when the initial value must be *computed* per instance rather than copied
from a template, since the declaration itself runs once, at class-body time:

    class Search(sl.Component):
        query: str = sl.state("")
        results: list[str] = sl.state([])
        opened_at: Instant = sl.state(factory=Instant.now)

        @sl.computed
        def title(self) -> str:
            return f"{len(self.results)} results for {self.query}"

computed caches until the component tree invalidates. batch coalesces related writes.
transaction restores every touched field if an exception escapes, and `sl.discord.Mount`
dispatch wraps mutating actions in one.

That guarantee reaches declared state, and only declared state:

| Attribute | Re-renders on write | Rolled back on failure |
|---|---|---|
| `sl.state(...)` | yes, including nested list, dict, and set mutation | yes |
| `sl.state(copy="ref")` | on assignment | to the previous reference |
| a plain attribute | no | no |

A plain attribute assigned during a transaction is therefore uncovered, so the framework says
so: a read-only action raises `ReactiveWriteError`, and a mutating one logs a warning naming
the attribute. `sl.strict_state()` turns that warning into `UndeclaredStateError`; the test
suite runs with it on. Declare the field to make it stop.

A component *created* during an action is exempt, because a transaction restores the view the
action started from and such a component had no state then. Handlers are free to build one.
The rule is birth, not mounting: a component built earlier and not currently in the tree is
still covered, since it may be about to go back in.

Neither rollback nor invalidation reaches a change made *through* a field — setting an
attribute on the object a `copy="ref"` field holds, for instance. Nothing can observe that, so
say it explicitly:

    async def _door_changed(self, event: sl.ChoiceEvent) -> None:
        self.build.door_orientation = event.selected[0]
        self.mutated("build")

`mutated` only schedules the draw; the change is still outside the transaction. Naming the
field is the point — the call fails if that field stops being declared state, so the manual
signal cannot drift away from the declaration it depends on.

state(persist=False) marks runtime-only data that durable snapshots omit. Persistent state
must be JSON-safe. `sl.state(copy="ref")` covers the opposite case, a collaborator that is
real state but must never be copied — a service, a guild, a session. It is never persisted,
and it snapshots the reference rather than a deep copy:

    class Panel(sl.Component):
        page: str = sl.state("server")
        guild: discord.Guild = sl.state(copy="ref")

        def __init__(self, guild: discord.Guild) -> None:
            self.guild = guild

`sl.state()` with neither a default nor a factory declares a field that `__init__` assigns;
reading it before then raises AttributeError.

Children appear through explicit keyed boundaries:

    def render(self):
        return sl.group(
            self.embed(self.filters, key="filters"),
            self.embed(self.results, key="results"),
        )

`sl.runtime.ComponentRuntime`, not `sl.discord.Mount`, owns rendering, keyed component identity, lifecycle,
invalidation, injected context, presentation state, and the bounded plan cache. Expansion
scopes action keys and pager keys, detects cycles and duplicate instances, and gives the
runtime deterministic `on_mount`/`on_unmount` ownership. Components have no mount reference;
the Discord mount is one frontend consumer of the runtime.

Presentation state is deliberately a closed vocabulary: `CursorState`, `SelectionState`,
`DisclosureState`, and `StrategyState`. It is per mounted message/viewer session and separate
from domain state. Generic cursors therefore do not leak into component fields, while apps
cannot store arbitrary operational objects in presentation snapshots.

Each runtime keeps a small callback-free plan LRU. Cache keys include semantic structure,
assets, target/version/limits, chrome, reservation, presentation/page state, nav factory
version, strictness, and search budget. Cache hits always recollect current callbacks,
including solver-generated pager controls.

## Actions and frontend adapters

Components receive PressEvent or SelectionEvent, not discord.Interaction. Events expose
portable actor facts and response intents: notice, present_form, download, redirect, and
finish. Each frontend implements ActionResponder; Discord details live in
sl.discord.ActionResponder.

A mount writes back through an `EditHandle` rather than a stored message: a way to reach one
already-sent message, and how long it is good for. The bot's own credentials never expire;
an interaction's do, and every click carries a fresh one, so `Mount` keeps the longest-lived
handle it has seen. A handle that no longer addresses its message raises `StaleHandleError`,
which is the one place webhook tokens and response shapes are understood. When no handle is
live the render waits in `Mount.pending` for the next interaction — `refresh()` has always
promised the next opportunity rather than the current instant.

| Policy | Concurrency | Stale control | State writes |
|---|---|---|---|
| EXCLUSIVE | serialized per mount | ignored and acknowledged | transactional |
| REBASE | serialized per mount | resolves newest binding | transactional |
| PARALLEL_READ | may overlap | allowed | rejected and rolled back |
| IMMEDIATE | may overlap | allowed | transactional; author accepts races |

Use EXCLUSIVE for ordinary mutations, REBASE when the same logical action should apply to
newest state after waiting, PARALLEL_READ for side-effect-free reads, and IMMEDIATE only when
concurrency is deliberately handled elsewhere.

## Pagination

Every paginator has an explicit unique string key. `sl.discord.Mount` stores a cursor per key; embedded
components prefix it automatically. The solver measures active footers and navigation IR to
a fixed point, so controls spend real text and component budgets.

A paginator scene record contains a content fingerprint. When content under one key changes,
`sl.discord.Mount` resets only that cursor; keyed anchors preserve the reader's page across insertions and
reordering where possible. `per=N` is count-based pagination; the default fills by target text
budget. Semantic Choices, Items, Navigation, and large Actions use keyed 25-option windows.
All use the same `NavFactory`.

Root structural pagination is opt-in: return `Document(..., key="screen")` from the root
component. If top-level structure still exceeds the component limit after lossless adaptation,
the planner partitions it into measured whole-message pages. Local pagination has precedence.
If a document needs active local and root pagers simultaneously, planning fails with remedies;
the engine never presents two competing navigation systems.

## Scenes and renderers

SceneDocument is immutable and contains no callbacks or native frontend objects.
PlanResult.bindings and PlanResult.resources are ephemeral side tables for a live frontend.

`sl.scene.Codec` provides canonical JSON, fingerprints, and a Draft 2020-12 schema through `schema`
and `schema_json`. Protocol 1 is current; incompatible changes increment the protocol.

sl.html.Renderer emits escaped semantic markup, action identifiers, policies, and pager metadata.
Standalone mode includes Discord-like CSS. It preserves planned structure; pixel-level
fidelity also needs the website's chosen Discord-markdown and emoji renderer.

## Durable mounts

Durability is opt-in:

1. Register a stable root key, positive version, and host factory in ComponentRegistry.
2. Build and attach the mount to MountManager.
3. Checkpoint at a host-chosen durability boundary.
4. Restore through the registry and a SnapshotStore implementation.

Snapshots contain JSON-safe declared state by keyed component path plus the closed
presentation vocabulary. They never contain callbacks, native items, service objects, or
dynamic import instructions. The factory injects dependencies. Component and adapter versions
are independent: an adapter update resets only its sticky strategy. Version or tree-shape
mismatches fail with `SnapshotError` and require an explicit host migration.

MountManager starts no tasks. Database or Redis storage, checkpoint cadence, expiry, and
reconnection remain host policy. A `DurableMountRecord` may pair the snapshot with a portable
`MountLocator`, for example Discord channel/message IDs, plus an expiry. Stores that implement
`LeaseSnapshotStore` add atomic claim, renew, and release operations. `MountManager.recover()`
claims live records and returns each restored mount beside its locator; the host reconnects the
frontend and periodically calls `renew_claims()` under its own task supervisor. This prevents
two workers from dispatching the same visible controls after a restart.

## Deliberate boundaries and current gaps

- Modal submission still uses the Discord modal adapter. A portable form protocol is future
  work.
- Exact `primitives.SelectMenu` overflow is intentionally a planning error; semantic
  interactions own legal paging. Cross-page multi-select needs an explicit grouping or commit
  model and is rejected rather than approximated.
- An ephemeral message that nobody has interacted with for over 15 minutes cannot be
  edited out of band at all; Discord expires the only credentials that reach it. Interactive
  use is unaffected, and `Mount.pending` reports a render held back for this reason.
- HTML action transport is not prescribed. Markup exposes action IDs; HTTP or WebSocket
  routing and authentication belong to the host.
- The base distribution is dependency-free; `squid-layouts[discord]` installs discord.py and anyio for the adapter.
