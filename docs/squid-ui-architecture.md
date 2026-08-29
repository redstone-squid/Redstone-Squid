# squid-ui architecture and API interactions

squid-ui separates UI intent, target planning, and drawing. Discord is one frontend,
not the data model: the same semantic document can be planned independently into a Discord
scene or a native HTML scene, serialized to JSON, and handed to another process.

## Concepts at a glance

Seven buckets, and every API in the package belongs to one of them. The rest of this document
expands them in roughly this order.

| Bucket | What it decides | Principal APIs | Expanded in |
|---|---|---|---|
| Rendering | what the user sees | `Document`, `plan`, target adapters, `sl.scene.Scene`, `Renderer` | [Semantic authoring](#semantic-authoring-adaptation-and-exact-primitives), [Scenes and renderers](#scenes-and-renderers) |
| State | what one component knows | `sl.state`, `sl.computed`, `sl.resource` | [Components and reactivity](#components-and-vue-inspired-reactivity) |
| Shared state | what several panels agree on | `SharedState`, `SharedStatePool`, `TopicBus` | [Shared state across message roots](#shared-state-across-message-roots) |
| Actions | what a press is allowed to do | `sl.action_control`, `Guard`, `history`, `ActionMiddleware` | [Actions and frontend adapters](#actions-and-frontend-adapters) |
| Lifetime | how long a panel lives and who may use it | `MessageRoot`, `SessionSpec`, `SessionManager`, `AccessPolicy` | [Which entry point to use](#which-entry-point-to-use), [Ownership and lifetime](#ownership-and-lifetime) |
| Durability | what survives a restart | `DurableSessionRuntime`, `Router`, `PersistentStatePool` | [Durable sessions](#durable-sessions), [Durable route graph](#durable-route-graph-and-dispatch-onion) |
| Diagnostics | what happened | `Profiler`, `DevTools`, `sd.testing` | package `README.md` |

The two rules that decide which bucket something lands in are in
[Ownership and lifetime](#ownership-and-lifetime): identity is never authority, and anything
owning background work ends through one named method.

## End-to-end flow

    Component state
        |
        | render()
        v
    semantic Document -- keyed components, assets, semantic content
        |
        +-- plan(..., Discord target)
        |       |
        |       v
        |   DiscordPlanner -- adaptation, search, measurement, pagination
        |       |
        |       v
        |   immutable Scene[Discord body] --> sd.Renderer
        |
        +-- plan(..., HTML target)
                |
                v
            HtmlPlanner -- native semantic resolution
                |
                v
            immutable Scene[HtmlBody] --> sl.html.Renderer

    Every PlanResult also carries PlanReport, PlanMetrics, and ephemeral ActionBindings.
    Every immutable Scene supports `sl.scene.Codec` JSON and JSON Schema.

The planner is the only layer allowed to choose an alternate, drop content, split a page, or
spend a target resource. A renderer is mechanical. If Discord drawing needs to clamp after
planning, that is a DrawInvariantError, not a second degradation mechanism.

## Which entry point to use

| Need | API | Result |
|---|---|---|
| Runtime for one discord.py client | `sd.install(client, defaults=..., bus=..., localization=...)` | `ClientRuntime`: sessions, scheduler, challenges, and the host localization hook |
| Current command or routed action | `inv = await sd.Invocation.of(source)` | source, runtime, localization, user, guild, and source-aware delivery |
| Terminal command reply | `await inv.reply(*nodes, visibility=...)` | a localized public, personal, or `sd.Private(reason)` response |
| Plain live panel | `await inv.mount(component, access=..., visibility=...)` | a delivered `MessageRoot` without session policy |
| Reusable application screen | `await MyScreen(...).show(source)` | the prepared screen, or `None` after a policy-authored rejection |
| Dynamic session composition | `await inv.open(component, spec, visibility=...)` | `Opened` or `Rejected`; destination and open context come from the invocation |
| Low-level root composition | `runtime.mount(...)`, then `root.send(inv.destination(...))` | explicit message-root and delivery ownership when a higher entry point cannot express it |
| Low-level Discord destination | `sd.reply_to`, `sd.respond_to`, `sd.send_to` | a `MessageDestination` for framework internals and transport adapters |
| Static Components V2 message | sd.render_static(document) | MessagePayload |
| One node as a detached item | sd.render_item(node, reservation=...) | discord.ui.Item for a host-built view |
| Static classic message | sd.classic.render_static(document) | MessagePayload |
| Region in a host-owned classic message | sd.classic.contribute(document, to=...) | AttachedClassicContribution |
| Discord message plus diagnostics | sd.render_message(document) | RenderedMessage |
| Portable planning | plan(document, target=...) | PlanResult |
| Native browser document | plan(document, target=sl.html.target()), then sl.html.Renderer().draw(scene) | semantic HTML string |
| Components V2 browser preview | sl.html.DiscordPreviewRenderer().draw(scene) | preview HTML string |
| Cross-process transport | sl.scene.Codec.dumps and loads | canonical protocol JSON |
| Resume an opted-in session | sd.durability.DurableSessionRuntime | recovered Session graph |
| Stateful root on a message the bot owns | `sd.edit_to(message)` | `MessageDestination` writing that message |

sd.render_message is the Components V2 convenience path: plan for `DISCORD_V2_DPY27`, draw with
`V2Renderer`, then strictly audit the result. `sd.classic.render_message` is its counterpart
for `DISCORD_V1_DPY27` and `ClassicRenderer`; both return a `RenderedMessage` containing the plan and its complete `MessagePayload`. There is no default — the author picks the
target, because the two modes differ in what a message can carry. Detached composition passes a
reservation, measured from the host view rather than counted by hand; composing the complete
document is preferable because the planner can see every cost. A reservation is applied by
planning against a reduced target, so adaptation and measurement agree on the room available. It never adopts an arbitrary existing `discord.py` view: renderers own their
output object, so unknown pre-existing controls cannot undermine measurement.

`Invocation` is the product entry point for one Discord event. `Invocation.of` resolves the
installed runtime and host localization hook once inside the ambient dispatch scope; callers then
reuse its audience policy for static replies, plain mounts, and session opens. Router dispatch and
message-root action/submit dispatch establish that scope themselves, so a handler does not install
one. `inv.t(...)` is only for strings leaving the layout system, such as autocomplete or a native
modal API; layout nodes retain `TextLike` values and resolve them when their message root renders.

`Screen` is the declarative application layer over `Invocation`. A subclass places stable policy in
class variables. Root policy — `access`, `visibility`, `timeout`, `expiry`, `follow_topics`, and
`root_options` — applies to both direct and session openings. Setting `session_name` enables session
policy through `scope`, `admission`, `capacity`, `quota`, and `domain`; those fields are rejected on
a direct screen instead of being ignored. A fixed `access` policy defaults to the opener, while an
instance can override `resolve_access(invocation)` when constructor state or invocation context
decides access. `show()` claims one instance for one opening attempt and records its `opening`
invocation before delivery. Invocation-dependent loading uses the ordinary component `on_load()`
hook and therefore runs only when the screen reaches its first render. A rejected session returns
`None` only after its deferred policy notice has already been answered.

`SessionSpec` remains the composition recipe for dynamic policy: `scope` picks the collision key,
`admission` decides what happens on collision, `capacity` and `quota` bound membership, and `access`
builds the root policy from the open context. `SessionSpec.open`/`respond`, `SessionManager`, raw
`MessageRoot`, and `reply_to`/`respond_to` remain public lower layers for framework extensions and
transport adapters. Application handlers normally enter through `Invocation` or `Screen`, which
derive destination, sessions, open context, and localization from one source.

## Semantic authoring, adaptation, and exact primitives

The package root is semantic-first. Structural nodes are `Group`, `Stack`, `Cluster`,
`Section`, `Article`, and `Aside`; content includes `Heading`, `Paragraph`, `List`, `Fields`,
`Table`, `Roster`, `Quote`, `Code`, `Media`, `Details`, and metrics; interactions are `ActionControls`,
`Choices`, `Items`, `Navigation`, and `Grid`. These say what the information means and preserve
stable string keys, not which Discord widget must appear.

Author them through the lowercase factories — `sl.section(sl.heading(...), *children)`,
`sl.action_controls(*entries, key=...)`, `sl.action_control(label, handler, key=...)`. Semantic identity comes
first in reading order; runtime identity and configuration are keyword-only. `None`/`False`
children are skipped so `cond and node` composes, and bare strings or t-strings in a child
position become a `Paragraph`. Collections are unpacked by the caller. The dataclasses remain
the IR and remain public; the factories only normalize what authors write.

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

Host-owned ledgers remain values outside the renderer. `place_roster` is a pure, stable
allocator over immutable declarations; `sl.roster` renders its result with active localized
chrome. `sl.tally` similarly renders host-computed counts and composes existing Progress and
Choices semantics instead of storing votes. MessageRooted tally controls adapt between buttons and
selects, while routed tally controls use one `RoutedChoices` route for all option keys.

Spatial data has three explicit contracts. `sl.semantic.TableDisplay.MATRIX` is an authoritative dense
code-block representation. `sd.button_grid(*cells, ...)` returns exact Discord rows
and fails planning when that chosen shape exceeds Discord limits. `sl.grid(*cells, ...)` is
semantic: its sticky `discord.grid` strategy moves from button rows to a coordinate matrix and
select, then to a paged select. Every rung submits the same stable cell key in a
`SelectionEvent`; unavailable cells remain visible but cannot be selected.

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

Target-shaped nodes live under `squid_ui.primitives`. Their policies are explicit:

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

Discord Markdown is the default authored text dialect, not a structured inline-content tree. Bare
strings are trusted author markup. `md(t"Build {title}")` safely escapes Python 3.14 template
interpolations and neutralizes mentions; `plain()` requests literal text; `raw_md()` opts one
known-safe interpolation back into trusted markup. Scenes preserve the dialect so each renderer
can choose an appropriate Markdown implementation. The HTML renderer uses markdown-it-py's
`js-default` configuration with raw HTML disabled, converts only allowlisted tokens, and validates
links and images as recommended by its
[security guidance](https://markdown-it-py.readthedocs.io/en/latest/security.html).

### Declaring a component's dialect

A node's type says which targets can plan it. Semantic factories such as `sl.heading` and
`sl.paragraph` produce `RenderTarget` nodes that can be planned for Discord or HTML. Exact
`sl.primitives.Text` and `sl.primitives.Heading` nodes are Discord-shaped, as are all other
primitive nodes: `Panel`, `Section`, `Gallery`, `File`, `Sep` and `Thumbnail` are Components V2
only, while `Card` and `Content` are classic only. The mode is a *type parameter*, and it
propagates: a primitive container factory takes the meet of its children's modes, so one `Panel`
nested three levels down makes the whole document `ComponentsV2Target`, and planning that against
`classic()` or `sl.html.target()` is a static error rather than a runtime one.

That reaches `Component` through `render`, so a component that uses a dialect-specific
primitive declares which dialect it is for:

```python
class BuildPanel(sl.Component[sl.ComponentsV2Target]):
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
        return sl.stack(sl.heading("Build"), sl.primitives.Panel(...))
```

An unannotated `sl.Component` is portable: it may render semantic nodes and can be planned for
either frontend. `Component` is contravariant in its mode. The markers live at the package root:
`sl.RenderTarget`, `sl.DiscordTarget`, `sl.HtmlTarget`, `sl.ComponentsV2Target`, and
`sl.ClassicTarget`.

Two cases this does not catch, both deliberate. A container mixing a V2-only and a
classic-only child works in neither, but contravariance makes the union the solver's natural
answer, so pyrefly accepts it against both targets; basedpyright rejects it at the call, and
the planner rejects it at runtime. And widget content slots are dialect-erased, because
`normalize_content` classifies an `object` and has no static type to carry a mode.

Planning internals that walk any document take `AnyLayoutNode`, which is the deliberate
opt-out: they rewrite whatever they are handed and leave the dialect judgement to the
target's dialect.

## Patterns: one state machine, two shells

Reusable interaction patterns are authored as pure `state -> tree` state machines. Control and
content construction enter through `PatternControls`; a pattern never hard-codes `sl.action_control`, a
route id, or a frontend message root. The same specification therefore has two execution paths:

| Shell | State location | Controls | Interaction result |
|---|---|---|---|
| `ComponentShell` | its declared `pattern_state = sl.state()` | closure-backed `ActionControl`, `Choices`, and `FormTrigger` | mutate state and let the message root redraw |
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

`Agreement` deliberately sits beside the pure pattern catalogue rather than inside it. Its
transition is actor-keyed, so it is a mounted component with two explicitly non-persistent
state cells (`approved` and `resolved`). Participant display names are host data, actor identity
comes from the event, and `ActionMode.EXCLUSIVE` serializes approval and withdrawal. Discord
hosts should mount it under `sd.Users(...)`; the component repeats membership validation
as a frontend-neutral safety boundary and calls its resolution hook once at the threshold.

`SourceRankedList` is intentionally outside the two-shell catalogue. It is an async component whose
visible resource owns one immutable `LoadedWindow`; `WindowLoader` owns source-position ordering. Its
`SourceCapabilities` determine whether navigation is backward, whether numeric ranges are meaningful,
and whether totals are absent, approximate, or exact. A source always returns its resolved `Position`,
so anchor fallback is explicit. The message root's one `NavigationContext` factory renders controls for both
these windows and materialized planner cursors. Pending navigation retains the previous window, and a
failed request renders that stale window with retry chrome.

Route state still has to fit the target's custom-id budget. Large domain drafts should be represented
by a compact stored identifier; the shell deliberately does not hide a database or persistence
policy behind pattern state.

## Components and Vue-inspired reactivity

Components render synchronously from state. A state field is **replaced, never mutated in
place** -- an in-place change moves no version, so nothing would notice it. The type checker
holds that line rather than the runtime: `sl.state()` is overloaded so a `dict` default
declares `Mapping`, a `list` declares `Sequence` and a `set` declares `AbstractSet`, which
makes a concrete annotation and every mutating method a type error while the stored value
stays the one assigned. Reach for `state(factory=...)` when the initial value must be
*computed* per instance, since the declaration itself runs once, at class-body time; a plain
default needs no copy and is shared:

    class Search(sl.Component):
        query: str = sl.state("")
        results: Sequence[str] = sl.state([])
        channels: Mapping[str, int | None] = sl.state({})
        opened_at: Instant = sl.state(factory=Instant.now)

        @sl.computed
        def title(self) -> str:
            return f"{len(self.results)} results for {self.query}"

`{**self.channels, "log": 1}` replaces one key. For more than that, copy into a local `dict`,
mutate it, and assign it back; the last line is the ordinary write.

`computed` records what its body read and recomputes when one of those values moves --
nothing is declared, so a conditional dependency is exact. It is lazy: one nobody renders is
never evaluated, and one that raises fails where its value is used. `untracked()` reads
without subscribing. batch coalesces related writes. transaction rolls back every write if an
exception escapes, and `sd.MessageRoot` dispatch wraps mutating actions in one.

That guarantee reaches declared state, and only declared state:

| Attribute | Re-renders on write | Rolled back on failure |
|---|---|---|
| `sl.state(...)` | yes | yes |
| `sl.state(opaque=True)` | on assignment, or on `mutated()` | to the previous reference |
| `sl.state(...)` on an `sl.runtime.SharedState` | every message root that rendered it, through the bus | yes |
| a plain attribute | no | it cannot be written inside an action at all |
| anything written by `on_load` | it is what the first render reads | n/a -- no transaction is open |

A write inside an action **stages**: it lands in the transaction's overlay and becomes visible
when the action commits. The action reads its own writes, and so do the computeds downstream
of them; another task reading the same component across an `await` sees the committed value
until then. Rolling back is dropping the overlay.

Every transaction has a stable `ActionContext` and exactly one terminal outcome. A publishing
transaction validates every strong shared/replicated read by version, freezes its cell patches,
and prepares all participants under the runtime commit gate. Installing patches and synchronously
applying the prepared participants is the commit point. Reactive notifications, participant
finalizers, ledger sinks, and aftermath hooks run after the gate and cannot veto that truth. An
apply-phase exception is an adapter integrity defect and is reported as such, never disguised as
a safe rollback.

A read is **strong** -- it becomes a commit precondition -- when the action also writes that cell,
or when it was taken inside `strong_read()`. Read-and-write is compare-and-set and needs no opt-in.
Read-only reads are not validated by default: handlers routinely consult shared state to decide
something and then write something unrelated, and aborting those costs more than the write skew it
prevents. An action that does branch on shared state it will not write says so with
`strong_read()`, which makes it serializable over what it read. `relaxed_read()` is the way back
out of a strong read; `untracked()` independently opts out of dependency capture.

History consumes the immutable commit event. Physical inverses require the committed slot version;
semantic participants plan their own inverses. Undo and redo are fresh actions, and redo is based on
the actual undo commit. External effects use an idempotent compensation execution whose failure or
partial success remains inspectable. The complete pipeline and examples are in
[`docs/action-ledger.md`](action-ledger.md).

## Shared state across message roots

`sl.state()` is per-component and per-message-root. When two live panels must agree on something the
*view* owns -- a filter, a selection, a theme -- declare an `sl.runtime.SharedState` namespace instead:

    class Appearance(sl.runtime.SharedState[int]):
        accent: int = sl.state(DISCORD_BLUE)
        density: str = sl.state("comfortable")

    appearance = Appearance(bot.topic_bus, user.id)

State on a namespace is `sl.state()` one level out and is literally the same storage, so replacement,
the equality no-op, `opaque=`, staging and rollback all behave identically. Two differences:
a write publishes the cell's `(handle, descriptor)` address on the bus instead of invalidating
one component, and a strongly read shared cell -- one the action also writes, or reads inside
`strong_read()` -- carries its version as a commit precondition when the action publishes anything.
If someone else moves it meanwhile -- including A→B→A -- the
action raises `sl.runtime.ReactiveConflictError` and publishes nothing. `Chrome.changed_elsewhere` is the wording
for that, shown through `handle_error` or an `ActionMiddleware`.

There is no global store and no lookup by type: two panels converge because something handed
them the same object, by constructor injection or `ContextKey`. That also settles lifetime -- the
handle *is* the state, so panels holding it means it dies with the last panel, and a cog or session
holding it means it survives every panel opening and closing. When what a host holds is *one handle
per scope*, `sl.runtime.SharedStateStatePool` writes that lifetime down where it is known instead of leaving a
`setdefault` cache around every namespace; see below. A message root subscribes to exactly the cells
its latest render read, reconciled at stage time, and `sl.runtime.addresses(lambda: appearance.accent)`
names an address by hand for a host that wants to follow one itself. A message root repaints its own
writes inside the interaction that made them -- `MessageRoot.observed` is what it rendered,
`MessageRoot.followed` what it managed to subscribe to -- so a missing scheduler costs live updates
from *other* message roots and nothing else. Nothing durable belongs
here; anything the application would still want with nobody looking at it is a service.

### Pooling one namespace per scope

A pool is strong and single-typed: it owns one `SharedState` subclass and retains one canonical handle
per hashable scope until that scope is dropped, the pool is cleared, or the pool itself is released.

    self._appearance = sl.runtime.SharedStateStatePool(Appearance, bot.topic_bus)
    ...
    appearance = self._appearance.get(scope)

`get(scope)` is get-or-create and synchronous, so nothing awaits between the miss and the insert.
`get_existing`, `delete`, `clear` and `active` are the rest of the surface. Where the pool is held *is*
the retention policy -- on the bot for process lifetime, on a cog for extension lifetime, on a
session for that session's. `squid/bot/layout_showcase.py` keeps one on the cog for exactly that
reason. None of this changes `SharedState` itself: constructing a handle and passing it directly stays
supported, and a scope used outside a pool may still be mutable or unhashable.

The scope a pool keys on is the one a `SessionSpec` already computes.
`OpenContext.of(interaction)` yields the context, and asking it for a kind statically --
`open_context.user_guild()` is a `UserGuildScope` -- lets a `SharedState[UserGuildScope]`
pool refuse the wrong scope at the call site. A panel holding its session key reaches a pool
through `key.scope` with nothing to convert. `OpenContext` and `ScopeKind` are deliberately
not on `sd`; import them from `squid_ui_discord.session_specs`.

`squid_storage.PersistentStatePool` is the hydrating variant, for a namespace that should survive a
restart: `await load(scope)` in place of `get(scope)`, `run()` as the background writer, and
`flush`/`close` to end it.

`sl.resource` is a descriptor-owned, runtime-only state machine rather than snapshot state:

    class Search(sl.Component):
        query: str = sl.state("")

        @sl.resource
        async def results(self) -> tuple[Result, ...]:
            return await index.search(self.query)

        def render(self):
            match self.results.status:
                case sl.resources.Pending(previous=previous): ...
                case sl.resources.Failed(error=error, previous=previous): ...
                case sl.resources.Ready(value=results): ...

The loader's reads are tracked the way a computed's are, so the state it consults is its
dependency set and a committed write to any of it re-pends the resource at the next read. A
resource whose loader has not run -- one holding a `.replace(value)` result -- presumes it
reads every field its component declares, and narrows to the truth after its first real load.
Render observation keeps hidden resources lazy. The default explicit policy commits the
`Pending` branch before settling it; `PendingMode.ATOMIC` settles the same state machine before
delivery. Siblings settle concurrently under the frontend's task group, and newly revealed resources
are discovered on the next bounded render pass. `.reload()` is awaited sugar over the same transition;
`.replace(value)` publishes an authoritative local result.

`sl.operation` declares a repeatable definition. `.start()` creates a fresh execution ID and one-shot
status machine, causally linked to the current action when present. Its explicit progress capability
updates `Pending`, and `Succeeded`, `Failed`, or `Cancelled` is terminal for that execution. Components
store and render an execution with a `match`; retries start another execution, and there is no detached
operation task.

A plain attribute assigned during a transaction is therefore uncovered, and the framework
refuses it: `UndeclaredStateError`, or `ReactiveWriteError` in a read-only action. It raises
*before* the write lands, which is the point -- a landed write is exactly the one thing a
rollback would leave standing. Declare the field to make it stop.

A component *created* during an action is exempt, because a transaction restores the view the
action started from and such a component had no state then. Handlers are free to build one.
The rule is birth, not mounting: a component built earlier and not currently in the tree is
still covered, since it may be about to go back in.

A state value is never mutated in place, so the only field whose contents can change behind
the framework's back is an `opaque=True` one -- setting an attribute on the collaborator it
holds. Neither rollback nor invalidation reaches that, so say it explicitly:

    async def _door_changed(self, event: sl.ChoiceEvent) -> None:
        self.build.door_orientation = event.selected[0]
        self.mutated(self.build)

`mutated` moves the holding field's version and schedules the draw; the change is still
outside the transaction. It takes the object rather than a field name -- identity finds the
field, which is how an opaque field settles anyway -- so the call is typed, and it fails if
no opaque field holds the object, so the manual signal cannot drift from the declaration.

state(persist=False) marks runtime-only data that durable snapshots omit. Persistent state
must be JSON-safe. `sl.state(opaque=True)` covers the opposite case, a collaborator the
component holds and never mutates -- a service, a guild, a session. It settles on identity
rather than equality, and it is never persisted:

    class Panel(sl.Component):
        page: str = sl.state("server")
        guild: discord.Guild = sl.state(opaque=True)

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

`sl.runtime.ComponentRuntime`, not `sd.MessageRoot`, owns rendering, keyed component identity, lifecycle,
invalidation, injected context, presentation state, and the bounded plan cache. Expansion
scopes action keys and pager keys, detects cycles and duplicate instances, and gives the
runtime deterministic `on_mount`/`on_unmount` ownership. Components have no message-root reference;
the Discord message root is one frontend consumer of the runtime.

`async def on_load(self)` is where a component fetches what it cannot render without. The
frontend awaits it before the first delivery that would show the component, once per instance,
and **before `render()` is ever called on it**: expansion stops at an embedded component that
still owes a load, so the tier is loaded and then re-rendered rather than rendered empty. The
delivered view is therefore the loaded one -- one delivery, no loading paint, and no `load()`
for a call site to forget. Siblings in a tier load concurrently; a raise delivers nothing and
leaves the load eligible to retry. `MessageRoot.send` and `refresh` load; `finish`,
`finish_via` and `_stage_view` deliberately do not. Use `sl.resource` for reactive async data whose
pending, stale, or failed states the component can render. `on_load` remains the imperative, atomic
hook for initialization that must finish before the component can render at all.

Presentation state is deliberately a closed vocabulary: `CursorState`, `SelectionState`,
`DisclosureState`, and `StrategyState`. It is per message rooted message/viewer session and separate
from domain state. Materialized cursors therefore do not leak into component fields, while apps
cannot store arbitrary operational objects in presentation snapshots. Resource state is likewise
runtime-only: it is an input to synchronous rendering, not durable domain or generic presentation
metadata.

Each runtime keeps a small callback-free plan LRU. Cache keys include semantic structure,
assets, target/version/limits, chrome, reservation, presentation/position state, nav factory
version, strictness, and search budget. Cache hits always recollect current callbacks,
including planner-generated pager controls.

## What each primitive promises

Four primitives carry the whole model, and each owes one thing:

    computed     repeatable synchronous derivation
    resource     repeatable asynchronous derivation; safe to cancel and restart
    operation    effectful execution; never implicitly restarted
    action       atomic over reactive state and enlisted participants -- and nothing else

The obligations are what make the rest follow. A loader may run zero times, once, or many
times -- a hidden resource never loads, a moved dependency re-pends one that did, and a
superseded load is discarded, or stopped outright where a host installs
`abandon_superseded_loads` as `sd` does -- so an irreversible effect inside a loader is
programmer error, not a supported pattern. Supersession is not failure: `Failed` is reserved for a loader that
raised. That is why effects belong in an operation, which the runtime never re-arms on its own;
retrying is the author starting another execution.

`action` is where the promise is narrowest and most often misread. **External effects are not
rolled back.** An action is atomic over cells and over participants that enlisted in the commit
gate, and a network call is neither. The supported shape is that the transaction commits the
*intent* and an operation the action arms reaches the terminal outcome, which a later action
records; `on_action_rollback` is how an author finds out that the effect already happened and
the commit did not.

The same narrowness explains why transactional state cannot be a loading indicator. Writing
`self.saving = True` inside a handler stages it, so it is invisible until commit -- by which
point it means nothing. `sl.Feedback` covers the fixed case from outside the transaction, and an
operation's progress covers the author-controlled one, for the same reason.

## Actions and frontend adapters

Components receive PressEvent or SelectionEvent, not discord.Interaction. Events expose
portable actor facts and response intents: notice, present_form, download, redirect, and
finish. Each frontend implements ActionResponder; Discord details live in
sd.ActionResponder.

What a delivery moves is a `MessagePayload`: mode, content, embeds, view and assets as
one value, so the payload Squid owns can be staged, logged and asserted on rather than
assembled kwarg by kwarg. `MessageMode` is `CLASSIC` or `COMPONENTS_V2`; construction rejects
the combinations Discord answers with an unhelpful 400, including a classic view that reports
`has_components_v2()` and would therefore set the flag implicitly. `MessageDestination` and
`EditHandle.write` both take one. Only `COMPONENTS_V2` is constructed today; `CLASSIC` exists
so the transition matrix is written once, and a `LayoutView` message can never go back to it —
that raises `MessageModeError` before the request.

A message root writes back through an `EditHandle` rather than a stored message: a way to reach one
already-sent message, and how long it is good for. A handle also records which mode the
message is in, so the legacy fields a pre-Components-V2 message must clear are stated rather
than guessed, and durable records carry the mode beside the locator. The bot's own credentials never expire;
an interaction's do, and every click carries a fresh one, so `MessageRoot` keeps the longest-lived
handle it has seen. A handle that no longer addresses its message raises `StaleHandleError`,
which is the one place webhook tokens and response shapes are understood. When no handle is
live the render waits in `MessageRoot.pending` for the next interaction — `refresh()` has always
promised the next opportunity rather than the current instant.

Cross-root refresh uses a payload-free `sl.runtime.TopicBus`: a topic is an exact hashable address,
not state. Subscribers re-read application services before asking their message root to refresh, so the
data layer remains the only source of truth. `LocalTopicBus` delivers synchronously and isolates
subscriber failures through a reporting hook; MessageRootScheduler scheduling coalesces per message root, and different
mounts refresh concurrently without one message root rendering over itself. The host supervises
`MessageRootScheduler.run()` explicitly. Subscriber tests publish and assert immediately.

Publish from the existing committed-change funnel or durable change-feed drain. Never attach the
bus to a message already owned by a durable reconciliation loop: that creates a second writer. In
this bot, build panels follow `("build", str(build_id))`, while posted build cards remain solely
owned by the Discord reconciliation queue. The same queue drain publishes after a successful
reconciliation.

The bus is process-local, so a write made in another process reaches it through a host-owned
bridge rather than a subscription. `sd.durability.PostgresTopicBridge` is that bridge over
`LISTEN`/`NOTIFY`: it takes a host `sl.runtime.TopicCodec`, publishes an encoded address and never state,
drops its own notifications by process origin, and calls `TopicBus.publish` for everything it
receives — so it composes with the bus contract instead of relaying it, and an address the codec
cannot name stays local. In this bot the vocabulary is `squid.topics.ResourceTopicCodec`, and the
worker publishes a build's topic when its schematic render lands, so a panel repaints without a
click. Delivery is a latency hint exactly like a local publish: the reconciler's poll is still
what makes the projection converge.

A followed message root with expiring interaction credentials is swept before its handle dies. Its final
reachable render includes “Live updates paused — press any control to resume”; an accepted click
renews the handle, clears the framework-drawn status, and flushes current state. Background edits
retain the remaining idle timeout rather than restarting the message root's lifetime.

| Policy | Concurrency | Stale control | State writes |
|---|---|---|---|
| EXCLUSIVE | serialized per message root | ignored and acknowledged | transactional |
| REBASE | serialized per message root | resolves newest binding | transactional |
| PARALLEL_READ | may overlap | allowed | rejected and rolled back |
| IMMEDIATE | may overlap | allowed | transactional; author accepts races |

Use EXCLUSIVE for ordinary mutations, REBASE when the same logical action should apply to
newest state after waiting, PARALLEL_READ for side-effect-free reads, and IMMEDIATE only when
concurrency is deliberately handled elsewhere.

`MessageRoot(..., middleware=(...))` installs application middleware directly; callers do not build a
pipeline object. The message root freezes that sequence and treats the same instance repeated in it as
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
short-circuit still returns to the message root's acknowledgement/flush path, and the watchdog, Discord
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

Every paginator has an explicit unique string key. `sd.MessageRoot` stores a cursor per key; embedded
components prefix it automatically. `measure()` costs active footers and navigation IR to
a fixed point, so controls spend real text and component budgets.

A paginator scene record contains a content fingerprint. When content under one key changes,
`sd.MessageRoot` resets only that cursor; keyed anchors preserve the reader's page across insertions and
reordering where possible. `per=N` is count-based pagination; the default fills by target text
budget. Semantic Choices, Items, Navigation, and large Actions use keyed 25-option windows.
All use the same `NavFactory`.

A `NavFactory` receives `on_previous`, `on_next`, and `on_seek`. `on_seek` takes a zero-based
page and is present only where the cursor can address one: always for a materialized cursor,
and for a source window only when it declares `SourceCapabilities.jumpable` with an exact count.
It is a page rather than a `Position` because `NavigationState.position.offset` is a page index
for a materialized cursor but an item offset for a source window; `NavigationState.page` is the
comparable one, and pairs with `extent`. The stock `default_nav` draws no jump control, since a
select costs a whole component row on every paginator in the process; `sd.page_select_nav`
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

`sl.scene.Scene` is immutable and contains no callbacks or native frontend objects.
PlanResult.bindings and PlanResult.resources are ephemeral side tables for a live frontend.

`sl.scene.Codec` provides canonical JSON, fingerprints, and a Draft 2020-12 schema through `schema`
and `schema_json`. Protocol 1 is current and additively includes `HtmlBody`; incompatible changes
increment the protocol.

`sl.html.Renderer` accepts only `Scene[HtmlBody]`. It mechanically emits escaped, accessible
semantic markup with native forms and controls plus action, route, selection, and pager metadata.
Fragment output is the default; standalone mode adds a document shell, escaped title, locale,
viewport metadata, and neutral colour-scheme-aware responsive CSS. Caller-supplied CSS is trusted
host configuration. There is no raw-HTML scene node, unrestricted scene style attribute,
JavaScript runtime, or transport implementation.

`sl.html.DiscordPreviewRenderer` preserves the former browser preview for Components V2 scenes.
It is useful for Discord tooling but is not an HTML planning target and cannot draw `HtmlBody`.

### How a traversal dispatches

Every node representation here — semantic, primitive, scene, and each renderer's private draw
program — is a closed PEP 695 union of behaviour-free frozen dataclasses, and every pass over one is
a `match` proven exhaustive by an `assert_never` terminal arm where the walked union is closed, or
closing with a structured raise where it is not. Nodes carry no `accept` method and there is no
type-to-handler registry; a pass that needs a different shape lowers to a new union instead. Extend
the open node set through `target.extensions`, and attach per-case behaviour through
`GeneratedHandler`. See [ADR 0075](decisions/0075-planner-dispatch-style.md) for why, and for what
to do when a `match` grows too long or two dialects start sharing rules.

## Durable sessions

Durability is opt-in:

1. Register a stable recipe key, positive version, and complete message-root constructor in `ComponentRegistry`.
2. Construct `DurableSessionRuntime` with the live `SessionManager`, a fenced store, and a frontend adapter.
3. Start the runtime after Discord login and await recovery before gateway connection.
4. Open and attach durable message roots through the runtime so the first complete record and later checkpoints remain
   coordinated with visible Discord commits.

Snapshots contain JSON-safe declared state by keyed component path plus the closed
presentation vocabulary. One durable record owns the root and every attached child, including portable
frontend locators, parent links, and actor attribution. Records never contain callbacks, native items, service
objects, or dynamic import instructions. Restore recipes inject dependencies and explicit access policy.
Component and adapter versions are independent; missing sequential component migrations retain the record as
incompatible for operator action.

`DurableSessionRuntime.run()` owns recovery, claim renewal, runtime-commit checkpointing, bounded retries, expiry,
and shutdown release under a host-owned anyio task group. `DiscordFrontend` promotes public interaction delivery
to permanent bot-token authority and reconnects a complete graph before registering it for dispatch. Fenced
admission publishes the newcomer and retires selected durable victims atomically, while stale claim tokens cannot
renew, save, or delete after takeover. SQLite assumes coordinated host clocks; Postgres uses database time.

## Durable route graph and dispatch onion

`RouteGroup` is both the namespace root and the feature-composition unit; there is no special
namespace subtype. A root such as `RouteGroup("r")` reserves the gone-response prefix when passed
to `Router`, while its children render_message stable final identities immediately. Group structure,
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

For an ownership-first path from existing views and persistent controls into these adapter
boundaries, see the [Discord migration guide](https://github.com/redstone-squid/Redstone-Squid/blob/master/packages/squid-ui-discord/docs/migrating.md).

The portable seam is the semantic `Document`. The public planner delegates to the selected target
backend and shares only target-neutral resources, options, state, caches, and reports.
`DiscordPlanner` owns adaptation, measurement, search, pagination, Discord budgets, and exact
primitive conversion. `HtmlPlanner` resolves semantic nodes directly into `HtmlBody`, without the
Discord solver or its limits. Each renderer is mechanical and consumes only its own scene body.

Below the Discord scene, `renderer`, `message_root`, `delivery`, and `routing` form a
**discord.py adapter**, not a Discord-protocol adapter, and its dependencies sort into three strata:

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
| `interaction.response.is_done()` switches response vs followup writes | `_WebhookMessageHandle.write`, `respond_to` | message-root handle tests |

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

## Ownership and lifetime

Every abstraction here belongs to exactly one owner, and the useful design-review question is
no longer "which layer does this belong in?" but:

> Who owns this, when does that ownership begin, and what exact event ends it?

| Layer | Owner | Owns |
|---|---|---|
| Domain | the host application | facts that outlive the UI |
| Reactive | the cell's owner; a transaction, temporarily | values; writes, until it commits |
| Presentation | a component | one declarative projection |
| | a candidate | one prospective projection, until it is settled |
| Frontend | a message root | one message |
| | a session | a graph of message roots |
| | an `EditHandle` | temporary or permanent write authority |
| Async | the caller | a resource's or operation's execution |
| | a supervisor | long-running infrastructure tasks |
| Durability | a store | bytes |
| | a runtime | claims and leases |
| Stateless | a route | identity that outlives every live UI object |

Two rules fall out of the table, and both have caught real defects:

**Identity and authority are never the same value.** A `MessageAddress` says where a message is
and stays true forever; an `EditHandle` says what may be written to it and expires. Collapsing
them is what made `notice()` clobber the panel before plan 07.

**A resource's death is explicit.** Anything owning background work says so in its type, ends
through one named method, and refuses to acquire a task as a side effect of a read.
`PersistentStatePool` acquired one inside `load()` and released it inside `close()`, which anyio
refuses across tasks — the ordinary case, and no test reached it.

### Lifecycle verbs

Closed set. A seventh synonym for "it is over" puts the reader back where they started, so
`tests/architecture/test_naming.py` denies the obvious ones.

| Verb | Means | After it |
|---|---|---|
| `close` | terminal; the object rejects further operations | unusable |
| `detach` | remove external integration | still usable, disconnected |
| `finish` | user-visible lifecycle completion | the subject is done |
| `cancel` | abandon unfinished async work | settled as cancelled |
| `discard` | drop staged, unpublished work | back to unstaged |
| `run` | own tasks until cancelled or drained | returns; the owner is done |

`close` and `finish` both end *the object*, so no class has both. `run`, `discard` and `cancel`
name other subjects, which is why `PersistentStatePool` has `run` and `close`, and
`SubscriptionReconciler` has `discard` and `close`.

`Fragment.release` and `ActionParticipant.abort` sit outside the set on purpose: `release`
transfers ownership to the caller (and its docstring argues why it is not `detach`), the stores
release a *claim* rather than themselves, and `abort` is two-phase-commit vocabulary paired
with `prepare`.

### Naming

**The full dictionary is [squid-vocabulary.md](squid-vocabulary.md)**, which supersedes this
section's "verbs closed, nouns open" position and was applied across the six packages on
2026-08-26. What follows is the part that did not move.

Lifetime is carried by verbs, not nouns. A closed noun vocabulary was designed and rejected
twice, on the same measurement each time: the six packages export 555 classes with 273
distinct last words, 179 used exactly once, so the table would have had to reject
`Component`, `MessageRoot`, `SessionSpec` and `Chrome` or grow until it was not a table. What nouns
owe instead is consistency, which needs no dictionary:

1. **One meaning per word.** `MountSnapshot` named both a view of a live mount and the
   serialized state that outlives it, and both were exported from `squid_ui_discord`.
2. **A name uses the same word its own members use.** `MemorySnapshotStore`'s methods were
   `list_records`/`load` and its field was `_records`; only the class name said "snapshot".
3. **Identity and authority are never one type**, as above.

Two words that recur and are worth pinning:

- **`Snapshot`** — a read-only view of something still alive. If the subject can be gone and
  the value still means something, it is a `Record` (a serialized fact) or a `State` (the same,
  without the metadata).
- **`Store`** — owns bytes beyond the process, and its name says what it stores.

A type that defines a terminating verb, or hands out expiring authority, states what ends it
in one clause in its docstring. Everything else states nothing: a frozen value has no lifetime
to describe, and saying so is noise.

## Deliberate boundaries and current gaps

- Form schemas, parsing, validation, and submission events are portable. Discord presentation
  remains a modal adapter, including its native entity and file extension fields.
- Exact `primitives.SelectMenu` overflow is intentionally a planning error; semantic
  interactions own legal paging. Cross-page multi-select needs an explicit grouping or commit
  model and is rejected rather than approximated.
- An ephemeral message that nobody has interacted with for over 15 minutes cannot be
  edited out of band at all; Discord expires the only credentials that reach it. Interactive
  use is unaffected, and `MessageRoot.pending` reports a render held back for this reason.
- HTML action transport is not prescribed. Markup exposes action IDs; HTTP or WebSocket
  routing and authentication belong to the host. Action and form callbacks remain only in
  `PlanResult.bindings` and never enter a scene.
- The engine depends directly on `squid-reactivity` and `markdown-it-py`. Two leaf packages sit on
  it as independent siblings: `squid-ui-discord` for the discord.py adapter -- message roots,
  sessions, routing, durability, with `squid-ui-discord[durable]` adding `squid-storage` -- and
  `squid-ui-widgets` for the reusable application state machines. Neither imports the other.
  Discord protocol knowledge stays behind `DiscordPlanner`; HTML planning and drawing do not
  import Discord limits, primitives, or measured-layout types.
