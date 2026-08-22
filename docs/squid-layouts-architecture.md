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
    exact primitives -- one measured candidate per searched state
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
| Stateful Discord interaction | sl.discord.Mount(component, access=...) | lifecycle, access, events, paging, edits |
| Scoped live UI lifetime | sl.discord.SessionRegistry | root/child cascade, cardinality, replacement |
| Static Discord message | sl.discord.render_static(document) | discord.ui.LayoutView |
| Discord view plus diagnostics | sl.discord.compose(document) | Composition |
| Portable planning | plan(document, target=...) | PlanResult |
| Browser or preview drawing | sl.html.Renderer().draw(scene) | HTML string |
| Cross-process transport | sl.scene.Codec.dumps and loads | canonical protocol JSON |
| Resume an opted-in session | sl.discord.durability.DurableSessionRuntime | recovered Session graph |

sl.discord.compose is the Discord convenience path: plan for sl.discord.Target, draw with
sl.discord.Renderer, then strictly audit the result. Detached composition passes a
reservation, measured from the host view rather than counted by hand; composing the complete
document is preferable because the planner can see every cost. A reservation is applied by
planning against a reduced target, so adaptation and measurement agree on the room available. It never adopts an arbitrary existing `discord.py` view: renderers own their
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

`Choices`, `Items`, `Details`, and `Navigation` each hold a value, and one rule says who
owns it. Every one of them takes an `Ownership`: `sl.controlled(value, on_change)` means the
author owns it — their value wins on every render and the engine never touches the session —
and `sl.managed(initial)` means the engine owns it in the presentation session under the
node's key. `Managed.initial` is a seed, not a value: it applies on a session miss and is
ignored from then on, so an author who needs their value to keep winning wants `controlled`.
Ownership is a value rather than an inference from which fields were passed, so a node
cannot be half-controlled and the mode is readable at the call site.

The two paths persist differently, which matters when a snapshot is restored. Managed values
travel in the presentation vocabulary and are gated by the framework's protocol and adapter
versions; controlled values travel in the owning component's declared state and are gated by
the host's `component_version`. Both survive a restore; they fail incompatible restores
under different version gates.

Adapters choose among finite lossless strategies. Actions may be individual controls,
grouped pickers, or a paged picker. Thirty-six ungrouped actions become 25 and 11 options;
author-declared groups never merge. Choices, Items, and Navigation use keyed 25-option
windows. Cross-page multi-selection is rejected because a page-local Discord select cannot
honestly express that domain operation without an explicit grouping or commit model.

Strategy ranking is lexicographic rather than scalar: representation stability by
`Flexibility`, author display preference, pager count, transition distance, then stable path
and strategy identifiers. Per-adapter versions invalidate only that adapter's sticky state.
Choosing between alternatives is one search, not several. Semantic strategies, semantic
fallback branches, and primitive `Variants` rungs are all decisions in a single conditional
state graph the planner explores best-first; `measure()` evaluates exactly one concrete
primitive layout and never chooses anything. The graph is conditional because a decision only
exists once the branch containing it is selected, so nine axes behind an unopened fallback
cost nothing.

States are canonical: a decision the selected branches no longer expose is dropped, a newly
reachable axis opens at its cheapest candidate, and changing a semantic representation
discards the ladder positions that were measured against the old tree. Frontier entries are
ranked by the structural loss they already commit to, then by semantic strategy cost;
completed candidates are ranked by their measured loss merged with that structural loss. So a
lossless representation change is always tried before a fallback the author priced as loss.
If no wholly lossless candidate exists, author priority is compared before loss kind: semantic
substitutions beat truncation, truncation beats spilling entries, and spilling beats dropping
a whole node.

Search stops as soon as the frontier's lower bound cannot beat the best feasible incumbent.
The default budget is 512 `measure()` evaluations; root-pagination probes are packing rather
than optimization and are not counted. A ladder product too large for the remaining budget is
walked one deterministic component-saving step at a time instead. Exhaustion, and any use of
that guidance, returns the best incumbent and records `planner.search_fallback`.

Every candidate gets its own `CursorCoordinator` and lowering, so a rejected state leaves no
pagers, assets, events, bindings, or staged session writes behind. `PlanCache` stores the
winning fallback branches, strategy assignment, and ladder positions; a hit re-lowers only
those and recollects current callbacks without measuring again.

Target-shaped nodes live under `squid_layouts.primitives`. Their policies are explicit:

- `Truncate` and `Spill` shorten content only when the author wraps or configures it.
- `Alt`/`Alts` supply text ladders and per-entry drop priority.
- `Paginate` has an explicit key, measured footer/navigation chrome, and optional `min_fill`
  and `widows` break preferences.
- `Variants` supplies an ordered ladder of complete structural alternates for component
  pressure; rungs may be capability-gated, and the planner filters them after target lowering
  so the survivors number from zero for the rest of the search.
- `Drop` and `Never` make omission or non-degradation explicit.

Semantic helpers `truncate`, `spill`, `optional`, `fallback`, and `best_effort` grant the
same losses at intent level. `budget` adds a hard minimum reservation, preferred size, and
lossless stretch band. Consequential actions, status, and code are never silently lost.

Target-native features use Extension(kind, version, payload, fallback). A target adapter
prepares and measures the native resource once. Unsupported targets use the mandatory
portable fallback. Extension payloads in scenes are versioned and JSON-safe.

Discord Markdown is the default text dialect, not a structured inline-content tree. Bare
strings are trusted author markup. `md(t"Build {title}")` safely escapes Python 3.14 template
interpolations and neutralizes mentions; `plain()` requests literal text; `raw_md()` opts one
known-safe interpolation back into trusted markup. Scenes preserve the dialect so every
renderer can choose an appropriate Markdown implementation.

## Patterns: one state machine, two shells

Reusable interaction patterns are authored as pure `state -> tree` state machines. Control and
content construction enter through `PatternControls`; a pattern never hard-codes `sl.action`, a
route id, or a frontend mount. The same specification therefore has two execution paths:

| Shell | State location | Controls | Interaction result |
|---|---|---|---|
| `ComponentShell` | its declared `pattern_state = sl.state()` | closure-backed `Action`, `Choices`, and `FormTrigger` | mutate state and let the mount redraw |
| `RouterShell` | caller-defined route parameters | `RoutedAction` and `RoutedChoices` | decode state and replace the complete document |

`PatternRoute(action, state, phase)` is the route-builder boundary. A deterministic button has
`phase="next"`; the pattern transition has already run and `state` is what the replacement document
renders. A select or form has `phase="input"`; its route carries the state the submitted values apply
to, and the handler passes those values to `RouterShell.transition`. Routed form handlers obtain the
prefilled schema from the pattern's `form_for` method. This distinction prevents a routed shell from
smuggling an in-process callback into a supposedly restart-surviving control.

Explicit pattern windows use `CursorCoordinator` position overrides, so route-carried positions outrank
stored cursors and are clamped by the same policy as planner-owned lists. The current pattern catalogue is:

- `TabsState.selected` and `MenuState.path` keep navigation stable by key.
- `RankedListState.position` preserves global rank numbers across explicit windows.
- `WizardState` retains answers by step key. Computed steps may hide answers without deleting them;
  returning to the branch restores their prefill, while `live_answers` excludes those orphans from
  Finish. A content-to-form Next can present the modal directly. Consecutive form steps necessarily
  render the next step's Continue trigger after submission because Discord forbids opening a modal
  from a modal-submit response.
- `MultiChoiceState` separates staged and committed sets. A visible-window submit replaces only that
  window's membership, group exclusions are applied during the merge, validation gates Apply, and an
  Apply edge dispatches only when the committed set changes. Panels with at most five options expose
  a form alternate to the planner.

`SourceRankedList` is intentionally outside the two-shell catalogue. It is an async component whose
visible resource owns one immutable `LoadedWindow`; `WindowLoader` owns source-position ordering. Its
`SourceCapabilities` determine whether navigation is backward, whether numeric ranges are meaningful,
and whether totals are absent, approximate, or exact. A source always returns its resolved `Position`,
so anchor fallback is explicit. The mount's one `NavigationContext` factory renders controls for both
these windows and materialized planner cursors. Pending navigation retains the previous window, and a
failed request renders that stale window with retry chrome.

Route state still has to fit the target's custom-id budget. Large domain drafts should be represented
by a compact stored identifier; the shell deliberately does not hide a database or persistence
policy behind pattern state.

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
| anything written by `on_load` | it is what the first render reads | n/a -- no transaction is open |

`sl.resource` is a descriptor-owned, runtime-only state machine rather than snapshot state:

    class Search(sl.Component):
        query: str = sl.state("")

        @sl.resource(depends=(query,))
        async def results(self) -> tuple[Result, ...]:
            return await index.search(self.query)

        def render(self):
            match self.results.state:
                case sl.Pending(previous=previous): ...
                case sl.Failed(error=error, previous=previous): ...
                case sl.Ready(value=results): ...

Dependencies are exact `sl.state` fields and invalidate the resource only when their transaction
commits. Render observation keeps hidden resources lazy. The default visible delivery commits the
`Pending` branch before settling it; `ResourceDelivery.ATOMIC` settles the same state machine before
delivery. Siblings settle concurrently under the frontend's task group, and newly revealed resources
are discovered on the next bounded render pass. `.reload()` is awaited sugar over the same transition;
`.replace(value)` publishes an authoritative local result.

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

`sl.state()` with neither a default nor a factory declares a field that `__init__` must
assign, the way a dataclass field with no default is required. Leaving one unassigned raises
TypeError at construction, not later at first read. Only the outermost `__init__` is checked,
so a subclass may assign after calling `super().__init__()`, and a class that has not
implemented `render` yet is exempt — it is a base to build on, and its subclasses do the
assigning.

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

`async def on_load(self)` is where a component fetches what it cannot render without. The
frontend awaits it before the first delivery that would show the component, once per instance,
and **before `render()` is ever called on it**: expansion stops at an embedded component that
still owes a load, so the tier is loaded and then re-rendered rather than rendered empty. The
delivered view is therefore the loaded one -- one delivery, no loading paint, and no `load()`
for a call site to forget. Siblings in a tier load concurrently; a raise delivers nothing and
leaves the load eligible to retry. `Mount.send`, `flush` and `refresh_now` load; `finish`,
`finish_via` and `_stage_view` deliberately do not. Use `sl.resource` for reactive async data whose
pending, stale, or failed states the component can render. `on_load` remains the imperative, atomic
hook for initialization that must finish before the component can render at all.

Presentation state is deliberately a closed vocabulary: `CursorState`, `SelectionState`,
`DisclosureState`, and `StrategyState`. It is per mounted message/viewer session and separate
from domain state. Materialized cursors therefore do not leak into component fields, while apps
cannot store arbitrary operational objects in presentation snapshots. Resource state is likewise
runtime-only: it is an input to synchronous rendering, not durable domain or generic presentation
metadata.

Each runtime keeps a small callback-free plan LRU. Cache keys include semantic structure,
assets, target/version/limits, chrome, reservation, presentation/position state, nav factory
version, strictness, and search budget. Cache hits always recollect current callbacks,
including planner-generated pager controls.

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

Cross-mount refresh uses a payload-free `sl.TopicBus`: a topic is an exact hashable address,
not state. Subscribers re-read application services before asking their mount to refresh, so the
data layer remains the only source of truth. Publishes coalesce per topic, reactor scheduling
coalesces per mount, and different mounts refresh concurrently without one mount rendering over
itself. The host supervises `TopicBus.run()` and `Reactor.run()` explicitly. `TopicBus.drain()` is
the deterministic no-background-task seam for subscriber tests.

Publish from the existing committed-change funnel or durable change-feed drain. Never attach the
bus to a message already owned by a durable reconciliation loop: that creates a second writer. In
this bot, build panels follow `("build", str(build_id))`, while posted build cards remain solely
owned by the Discord reconciliation queue. The same queue drain publishes locally after a
successful reconciliation, which carries database changes from other processes into live panels.

A followed mount with expiring interaction credentials is swept before its handle dies. Its final
reachable render includes “Live updates paused — press any control to resume”; an accepted click
renews the handle, clears the framework-drawn status, and flushes current state. Background edits
retain the remaining idle timeout rather than restarting the mount's lifetime.

| Policy | Concurrency | Stale control | State writes |
|---|---|---|---|
| EXCLUSIVE | serialized per mount | ignored and acknowledged | transactional |
| REBASE | serialized per mount | resolves newest binding | transactional |
| PARALLEL_READ | may overlap | allowed | rejected and rolled back |
| IMMEDIATE | may overlap | allowed | transactional; author accepts races |

Use EXCLUSIVE for ordinary mutations, REBASE when the same logical action should apply to
newest state after waiting, PARALLEL_READ for side-effect-free reads, and IMMEDIATE only when
concurrency is deliberately handled elsewhere.

`Mount(..., middleware=(...))` installs application middleware directly; callers do not build a
pipeline object. The mount freezes that sequence and treats the same instance repeated in it as
one installation, while separately configured instances of the same class remain distinct. The
first entry is outermost, completion unwinds in reverse, omitting `proceed()` short-circuits the
handler, and the continuation is valid once and only during its middleware call.

Middleware begins only after access, binding resolution, the concurrency gate, and stale-generation
handling admit the action. Plan 31's per-action guards belong immediately before middleware. Its
immutable `ActionRequest` carries the portable event, stable key, interaction kind, effective
policy, submitted and active generations, and a `rebased` flag. Rebase is metadata: a rebased action
may subsequently complete or fail, so it is not a terminal dispatch result.

The handler's reactive transaction is the onion endpoint rather than a wrapper around the whole
onion. An outer middleware may therefore catch a handler exception only after the handler's state
writes have rolled back. Middleware is application policy, not component code; it receives no
component or binding and should not mutate component state through captured references. A
short-circuit still returns to the mount's acknowledgement/flush path, and the watchdog, Discord
write, generation commit, and error presentation remain outside user middleware.

Form submissions run the same funnel, so REBASE resolves the newest binding there too: a
`FormTrigger` declares one per render, planning carries it in `PlanResult.form_bindings`, and
a late submission is rebased onto the newest one for its key. It is rebased only when that
binding parses the same field keys -- a schema that changed shape cannot read what the reader
typed. A form presented ad hoc from a handler has no render-declared binding, and a trigger the
newest render dropped has no newer one; both run what the reader submitted, since discarding a
filled-in form is the worse surprise. Note that the presenting button and the submission answer
to the same key in two different tables: `bindings` opens the form, `form_bindings` submits it.

## Pagination

Every paginator has an explicit unique string key. `sl.discord.Mount` stores a cursor per key; embedded
components prefix it automatically. `measure()` costs active footers and navigation IR to
a fixed point, so controls spend real text and component budgets.

A paginator scene record contains a content fingerprint. When content under one key changes,
`sl.discord.Mount` resets only that cursor; keyed anchors preserve the reader's page across insertions and
reordering where possible. `per=N` is count-based pagination; the default fills by target text
budget. Semantic Choices, Items, Navigation, and large Actions use keyed 25-option windows.
All use the same `NavFactory`.

A `NavFactory` receives `on_previous`, `on_next`, and `on_seek`. `on_seek` takes a zero-based
page and is present only where the cursor can address one: always for a materialized cursor,
and for a source window only when it declares `SourceCapabilities.jumpable` with an exact count.
It is a page rather than a `Position` because `NavigationState.position.offset` is a page index
for a materialized cursor but an item offset for a source window; `NavigationState.page` is the
comparable one, and pairs with `extent`. The stock `default_nav` draws no jump control, since a
select costs a whole component row on every paginator in the process; `sl.discord.page_select_nav`
opts into one, offering every page when there are at most 25 and an evenly spaced ladder across
the whole range beyond that.

`sl.paged(container, key=..., chars=...)` applies an author-sized budget and pages between the
container's heterogeneous children. Children are atomic unless a text child must split;
`sl.unbreakable` groups several lowered primitives into one atomic item and
`sl.keep_with_next` forbids the following break. Region breaks minimize preference violations,
then page count and squared fill badness. Text and heterogeneous regions share this exact
prefix-summed fragmentation engine at every input size; there is no size-dependent heuristic.
A section heading is kept with its first body child automatically. Region fingerprints hash
every child's stable logical identity, so callbacks do not make an otherwise unchanged page
stale across processes.

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

## Durable sessions

Durability is opt-in:

1. Register a stable recipe key, positive version, and complete mount constructor in `ComponentRegistry`.
2. Construct `DurableSessionRuntime` with the live `SessionRegistry`, a fenced store, and a frontend adapter.
3. Start the runtime after Discord login and await recovery before gateway connection.
4. Open and attach durable mounts through the runtime so the first complete record and later checkpoints remain
   coordinated with visible Discord commits.

Snapshots contain JSON-safe declared state by keyed component path plus the closed
presentation vocabulary. One durable record owns the root and every attached child, including portable
frontend locators, parent links, and actor attribution. Records never contain callbacks, native items, service
objects, or dynamic import instructions. Restore recipes inject dependencies and explicit access policy.
Component and adapter versions are independent; missing sequential component migrations retain the record as
incompatible for operator action.

`DurableSessionRuntime.run()` owns recovery, claim renewal, visible-commit checkpointing, bounded retries, expiry,
and shutdown release under a host-owned anyio task group. `DiscordFrontend` promotes public interaction delivery
to permanent bot-token authority and reconnects a complete graph before registering it for dispatch. Fenced
admission publishes the newcomer and retires selected durable victims atomically, while stale claim tokens cannot
renew, save, or delete after takeover. SQLite assumes coordinated host clocks; Postgres uses database time.

## Durable route graph and dispatch onion

`RouteGroup` is both the namespace root and the feature-composition unit; there is no special
namespace subtype. A root such as `RouteGroup("r")` reserves the gone-response prefix when passed
to `Router`, while its children compose stable final identities immediately. Group structure,
identities, and middleware freeze when the router registers; an existing identity may replace its
handler afterward so a discord.py extension reload remains safe.

Dispatch builds one middleware onion in deterministic order:

```text
acknowledgement watchdog and unhandled-error boundary
└─ router middleware (first attached outermost)
   ├─ matched: root group middleware
   │  └─ descendant group middleware, root to leaf
   │     └─ routed handler
   └─ unmatched reserved id: gone hook
```

Only router middleware applies to an unmatched id admitted by the reserved namespace. A matched
route additionally inherits every group attachment in its lineage. Each layer may perform work
before and after its one `proceed`, catch an inner failure, or short-circuit. The immutable
`RouteRequest` exposes the component kind, canonical `Route`, read-only converted parameters,
selected values, matched group, and whether an alias matched. It deliberately has no mutable
dependency bag.

The watchdog acknowledges an unused interaction response before Discord's three-second deadline
and immediately after an operation returns without responding. It does not claim that later
followups completed, and it never invents private thinking or modal semantics. Error presentation
is the single framework boundary outside the whole user onion.

## Library binding: discord.py, not Discord alone

The portable seam is the scene. Everything above it — semantic vocabulary, planner,
`measure()`, `CursorCoordinator`, components — binds to Discord's *shape* (budgets, option windows,
row widths) but imports no discord.py; `sl.html` consumes scenes. Everything below it —
`renderer`, `mount`, `delivery`, `routing` — is a **discord.py adapter**, not a
Discord-protocol adapter, and its dependencies sort into three strata:

- **Protocol facts** — component budgets, token lifetimes, callback-type restrictions,
  custom-id length, error codes 10015/10062/50027/50035. Depending on these is the
  adapter's job.
- **Library object model** — `Message`, `LayoutView`, `DynamicItem`, passed through as
  transport without interpretation. Mostly harmless.
- **Library behaviours** — the semantics of how discord.py *mediates* the protocol. This
  is the dangerous stratum: every externally audited defect to date lived here
  (`InteractionMessage.edit`'s hidden token routing, ViewStore scheduling), while the
  scene and planner layers, which touch only protocol facts, have produced none.

Stratum-3 inventory. Each entry is owed a pin test that exercises the real library, so a
discord.py upgrade breaks in the suite rather than in production — the same discipline
CLAUDE.md mandates for Nucleation, applied to discord.py:

| Behaviour relied on | Where | Pin |
|---|---|---|
| ViewStore schedules one call per matching dynamic-item class | Router's one-class design, exact-overlap rejection, register idempotence (plan 16) | overlap and registration pins in `test_routing.py` |
| `is_dispatchable() == False` keeps mounted routed controls out of ViewStore | single dispatch path for mounted controls | `test_routing.py` mounted double-dispatch assertions |
| `InteractionMessage.edit`/`WebhookMessage.edit` route through interaction endpoints; application followups force wait | edit-authority semantics; plan 23's defect and fix | real-library pins in `test_mount.py` |
| current modal controls serialize inside `Label` through `Modal.to_dict` | plan 18's Discord field ceiling and the host clamp gate | inventory pin in `test_form_discord.py`; host modal tests |
| `interaction.response.is_done()` switches response vs followup writes | `_WebhookMessageHandle.write`, `respond_to` | mount handle tests |

Policy:

- A discord.py version bump is a defined event: run the pins, review this inventory.
  `uv.lock` pins exactly, so bumps are always deliberate.
- **Durable artifacts speak protocol; in-process machinery may speak discord.py.** A
  routed custom id lives in posted messages for years, so route *identity* is
  protocol-only (plan 16 built it so); route *dispatch* is re-established at startup and
  may bind to the library.
- Feature ceilings are `min(protocol, discord.py)`, and the two release trains are
  independent. The modal field inventory (plan 18) is protocol-complete on discord.py
  2.7.1 (`Label`, modal selects, `FileUpload`, `RadioGroup`, `CheckboxGroup` all ship);
  re-verify both sides whenever a plan leans on a new component type.
- No library-abstraction layer. A second-library adapter has zero consumers, and the
  scene is already the seam one would attach to; below it the binding is admitted, not
  abstracted. The DynamicItem binding in particular is chosen, not accidental: a raw
  `on_interaction` router would have to peek into ViewStore to avoid double-handling
  live views, so the sanctioned hook wins and gets pinned instead.

## Deliberate boundaries and current gaps

- Form schemas, parsing, validation, and submission events are portable. Discord presentation
  remains a modal adapter, including its native entity and file extension fields.
- Exact `primitives.SelectMenu` overflow is intentionally a planning error; semantic
  interactions own legal paging. Cross-page multi-select needs an explicit grouping or commit
  model and is rejected rather than approximated.
- An ephemeral message that nobody has interacted with for over 15 minutes cannot be
  edited out of band at all; Discord expires the only credentials that reach it. Interactive
  use is unaffected, and `Mount.pending` reports a render held back for this reason.
- HTML action transport is not prescribed. Markup exposes action IDs; HTTP or WebSocket
  routing and authentication belong to the host.
- The base distribution is dependency-free; `squid-layouts[discord]` installs discord.py and anyio for the adapter.
