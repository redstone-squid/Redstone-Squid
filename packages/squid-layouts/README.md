# squid-layouts

A declarative, limits-aware UI engine with Discord Components V2 and HTML renderers.

Discord rejects any message that exceeds one of its many hard limits (4000 display characters,
40 components, 25 select options, 45-char modal titles, …) with an opaque HTTP 50035. This
package makes those failures unrepresentable: views describe *intent*, and the engine measures
every markdown prefix and code fence exactly, allocates the shared budgets by priority, and
degrades content the way its author said it should degrade.

```python
import squid_layouts as sl

class Counter(sl.Component):
    count: int = sl.state(0)

    def render(self):
        return sl.section(
            t"Count: {self.count}",
            sl.actions(sl.action("+1", self.increment, key="increment"), key="counter-actions"),
            heading="Counter",
        )

    async def increment(self, event: sl.PressEvent) -> None:
        self.count += 1  # the mount re-renders and edits the message
```

See [the architecture and API interaction guide](../../docs/squid-layouts-architecture.md)
for component composition, planning, renderers, action policies, and durable mounts.

## The layers

1. **Semantic documents** describe author intent with `Section`, `Paragraph`, `List`,
   `Fields`, `Table`, `Media`, `Details`, `Actions`, `Choices`, `Items`, and `Navigation`.
   Authors may express a display preference and flexibility, but never an exact Discord shape.
   The lowercase factories (`sl.section`, `sl.actions`, `sl.field`, …) are the recommended
   authoring surface; the uppercase dataclasses are the IR they build and stay fully public.
2. **Target adapters** select lossless representations. For example, 36 semantic actions become
   two pickers containing 25 and 11 options; explicit action groups never merge. Strategy state
   supplies hysteresis, so small data changes do not reshuffle a familiar UI.
3. **Exact primitives** live under `squid_layouts.primitives`. `Row`, `SelectMenu`, `Panel`,
   and their overflow policies are the deliberate target-shaped escape hatch, not the primary API.
4. **Planner and solver** rank strategies by coarse lexicographic tiers, measure every target
   resource, apply only author-granted loss, and produce a `PlanReport`. Search has a bounded
   512-state default; exhaustion emits `planner.search_fallback` and keeps a lossless plan.
5. **Scene protocol 1** is immutable canonical JSON with action references but no callbacks or
   native frontend objects.
6. **Renderers** mechanically draw a scene. Discord produces Components V2 and audits it with
   `sl.discord.conform(strict=True)`; HTML produces escaped Discord-like preview markup from the same scene.

`sl.discord.compose()` is the Discord convenience pipeline, with `reserved_text` for callers whose
message carries content the engine cannot see. It always creates a renderer-owned view;
adopting an arbitrary existing `discord.py` view is intentionally unsupported. Components nest through explicit
`self.embed(child, key=...)` boundaries, so actions and pagers never cross-wire. `sl.discord.Mount`
binds a component tree to a message: every
interaction funnels through it (author lock, error hook, re-render/edit), timeouts disable
controls, `sl.discord.Reactor` coalesces out-of-band refreshes, and `sl.discord.Navigator` stacks screens with
Back/Home/Close by composition. A mount's `nav=` replaces the stock Previous/Next row with
controls built from `sl.discord.NavigationContext`; the same factory receives materialized pages and
asynchronously loaded windows. Semantic pickers page through keyed
25-option windows. A keyed root `Document` may promote structural overflow to whole-message
pages; local pagination wins, and local plus root navigation are never shown simultaneously.
Authors can size a region with `sl.budget(node, min=..., prefer=..., stretch=...)` or page a
heterogeneous container with `sl.paged(section, key=..., chars=...)`. Use
`sl.keep_with_next(heading)` to prevent a stranded heading and `sl.unbreakable(group)` when a
region's children must stay together. These declarations use the same keyed cursor lifecycle as
text and option pagination.
`sl.discord.render_static` is the sessionless
path for reconciler-managed posts. `sl.discord.build_modal`/`sl.discord.conform_modal` do the same for modals,
whose string lengths discord.py does not validate at all. `sl.scene.Codec` transports plans to
other processes; `sl.discord.durability.DurableSessionRuntime` provides opt-in, whole-session recovery.

Presentation colours are an immutable `sl.Palette`, supplied to `sl.plan`, `sl.discord.compose`,
`sl.discord.render_static`, or `sl.discord.Mount`. An omitted section or article accent inherits
`Palette.brand`; `accent=None` explicitly opts out and an integer remains an exact data override.
Semantic tones resolve through the palette. `sl.themed(palette, *children)` scopes an override to a
subtree, so a component may select a palette from reactive state while the final scene still contains
only exact colours. Discord buttons retain Discord's platform-owned style colours.

### Live updates across mounts

`TopicBus` is a payload-free, process-local latency projection. Publishing says only that an
address changed; every subscriber re-reads the application's source of truth. It is not durable,
and queued topics disappear with the process. Exact matching means hosts should construct every
topic through one vocabulary function rather than mixing values such as `("build", 123)` and
`("build", "123")`.

```python
bus = sl.TopicBus()
reactor = sl.discord.Reactor(bus)
mount = sl.discord.Mount(panel, access=sl.discord.Owner(interaction.user.id), scheduler=reactor)
reactor.follow(mount, ("build", "123"))  # subscribe before the first read/send
await mount.send(sl.discord.respond_to(interaction))

# The host visibly owns both long-running coroutines.
async with anyio.create_task_group() as tasks:
    tasks.start_soon(bus.run)
    tasks.start_soon(reactor.run)
```

`publish()` is a synchronous enqueue for the event-loop thread. A distributed application bridges
its own durable change feed, NOTIFY listener, or queue consumer into the local bus. Publish where
the application already funnels committed changes; do not subscribe a durable projection that
already has a reconciler, because that would give one message two competing writers. For tests,
call `publish()`, then `await bus.drain()` and assert without starting background work or sleeping.
Expiry and idle-time tests can inject UTC and monotonic clocks through `Reactor(clock=...)` and
`Mount(clock=...)`; production callers normally keep their defaults.

## Interaction patterns and two shells

`Tabs`, `Menu`, materialized `RankedList`, `Wizard`, and `MultiChoicePanel` are pure state machines.
They do not choose between in-memory callbacks and restart-surviving routes. Instead, a shell injects
that control construction:

```python
tabs = sl.Tabs(
    (sl.Tab("summary", "Summary", summary), sl.Tab("history", "History", history)),
    key="build-tabs",
)

# A mounted message: state lives in sl.state and controls use sl.action closures.
mount = sl.discord.Mount(tabs.component(), access=sl.discord.Everyone())

# A restart-surviving message: state is decoded from and encoded into route parameters.
shell = sl.RouterShell(
    lambda request: BUILD_TAB.id(build_id=build.id, tab=request.state.selected),
)
document = shell.render(tabs, sl.TabsState(selected=tab))
```

Every mount states who may interact. `Owner`, `Users`, and `Everyone` cover static policy;
`Check` accepts an asynchronous application policy. Visibility stays a destination concern,
so owner access does not by itself make a channel message private.

`SessionRegistry` groups a root mount and its attached children under one operational lifetime.
Typed `SessionKey.user`, `guild`, `user_guild`, `global_`, and `custom` constructors name scoped
cardinality, while `SessionPolicy` composes a limit, collision selection, and replacement
protection. Opens return `Opened`, `Rejected`, or `Abandoned`; no preflight `get()` is needed to
explain a collision.

Stateful drafts that must survive restarts open through `DurableSessionRuntime`, which coordinates
fenced admission, recoverable Discord bindings, whole-session checkpoints, and lease supervision.
See [Durable sessions](docs/durable-mounts.md) for the imperative and `DurableBot` startup paths.

`PatternRoute.phase` is `next` for deterministic buttons: its state is already the state the next
document should render. Selects and forms use `input`, because their values arrive in the
interaction; a route handler calls `RouterShell.transition(...)` with those values and rebuilds the
whole document. `Wizard.form_for(...)` and `MultiChoicePanel.form_for(...)` return the form a routed
input action should present. The route builder owns compact state encoding and receives the normal
100-character custom-id budget check from `sl.routed_action`/`sl.routed_choices`.

Routed patterns accept frontend-neutral content only. A mounted shell may embed child `Component`
instances, but a process-independent route cannot carry an in-memory component identity.

### Durable routed controls

Controls on long-lived posts use a stable route tree rather than an in-memory mount. The namespace
is an ordinary root group; every `define` returns its final, context-free identity immediately:

```python
routes = sl.discord.RouteGroup[Bot]("r")
polls = routes.group("polls")
close_poll = polls.define("close", aliases=("poll:close",))

router = sl.discord.Router(namespace=routes, on_gone=control_gone)

@polls.route(close_poll)
async def close(interaction: discord.Interaction[Bot]) -> None:
    ...
```

Reusable policy is a `Middleware[BotT]` instance attached before `Router.register(client)`:

```python
class TraceRoutes(sl.discord.Middleware[Bot]):
    async def dispatch(self, request, proceed) -> None:
        with route_span(request):
            await proceed()

router.add_middleware(TraceRoutes())
polls.add_middleware(PollAuthorization())
```

Middleware forms one onion in first-attached, outermost order: router middleware, inherited
root-to-leaf group middleware, then the handler, unwinding in reverse. Omitting `proceed()`
short-circuits; calling it twice or after `dispatch` returns is an error. The router keeps the
initial interaction acknowledgement deadline outside this chain, so a slow handler or deliberate
short-circuit cannot produce Discord's generic interaction failure.

### Computed values

`sl.computed` caches synchronous derived values against explicit `sl.state` dependencies. A write
to any other state field may re-render the component, but it does not recompute the value:

```python
class SearchResults(sl.Component):
    query: str = sl.state("")
    filters: frozenset[str] = sl.state(frozenset())

    @sl.computed(depends=(query, filters))
    def visible_results(self) -> tuple[Result, ...]:
        return apply_filters(search(self.query), self.filters)
```

Dependencies may be `sl.state` or earlier `@sl.computed` descriptors declared on the same
component. Computed dependencies form selector boundaries: downstream computed values and
resources invalidate only when the refreshed value compares unequal. The explicit declaration is
required; there is no component-wide invalidation mode.

### Reactive async resources

`sl.resource` keeps async data in a synchronous, renderable state machine. Dependencies are the
actual `sl.state` or `@sl.computed` descriptors declared earlier in the same class:

```python
class VotingPanel(sl.Component):
    kind: VoteKind = sl.state(VoteKind.BUILD)

    @sl.resource(depends=(kind,))
    async def configuration(self) -> VoteConfiguration:
        return await votes.configuration(self.kind)

    def render(self):
        match self.configuration.state:
            case sl.Pending(previous=None):
                return sl.note("Loading…")
            case sl.Pending(previous=sl.Ready(value=config)):
                return refreshing_panel(config)
            case sl.Failed(error=error, previous=previous):
                return failed_panel(error, previous)
            case sl.Ready(value=config):
                return voting_panel(config)
```

A committed dependency change synchronously invalidates the resource. Hidden branches remain lazy:
only resources observed during rendering are loaded. `Pending` and `Failed` retain the last `Ready`
value when available, while request tokens prevent stale completions from publishing.

Visible delivery is the default: Discord commits the pending render, settles observed sibling
resources concurrently, then edits to the settled render. Use
`@sl.resource(delivery=sl.ResourceDelivery.ATOMIC)` when pending should remain an internal state and
the first delivery must already be settled. Both policies use the same state machine and neither
starts detached background work.

`await panel.configuration.reload()` is the caller-owned, awaited form. `invalidate()` requests a
new value without immediately loading it, and `replace(value)` publishes an authoritative local
result. Resource state is runtime-only and is not included in durable component snapshots.

### Async cursor sources

A ranking too large to materialize uses the distinct `SourceRankedList` component. A source declares
only what it can prove and fetches one window at a time:

```python
class BuildSource:
    capabilities = sl.SourceCapabilities(backward=True)

    async def fetch(self, position: sl.Position, extent: int) -> sl.Window[Build]:
        resolved, rows, has_previous, has_more = await builds.window(position, extent)
        return sl.Window(resolved, tuple(rows), has_previous, has_more)

ranking = sl.SourceRankedList(
    BuildSource(),
    key="builds",
    label=lambda build: build.title,
    value=lambda build: build.score,
    identity=lambda build: str(build.id),
    page_size=10,
)
```

`position.direction` is `Direction.AROUND` for an anchor-preserving refresh, `FORWARD` for rows after
the anchor, and `BACKWARD` for rows before it. Every `Window` returns the source's resolved
`Direction.AROUND` position, including its fallback when an anchor disappeared. The component exposes
loading and failure as visible resource states, retaining the previous window during navigation.
`WindowLoader` fingerprints visible identities, while the resource rejects superseded completions.

`SourceCapabilities` separately declares backward traversal, known offsets, arbitrary jumps, and
`CountPrecision`. Exact jumpable sources get page counts and an `on_seek` on their
`NavigationContext`, which takes a zero-based page; exact or approximate sequential sources get
range totals; offset-only sources get a range; keyset-only sources get no numeric footer. A source that
declares no count must return `total=None`. Source-backed rankings are components because fetching stays
outside planning and cannot run in `RouterShell.render()`.

## Host integration rules

- The base package has no dependencies. Install the `discord` extra for discord.py and anyio. The
  adapter never starts background work on its own — start `sl.TopicBus.run()` and
  `sl.discord.Reactor.run()` under your own supervisor.
- Factories take content positionally and everything else by keyword. `None` and `False`
  children are skipped, so `cond and node` is the way to include something conditionally;
  `True` is rejected because `and` can never produce it. Collections are unpacked by the
  caller: `sl.fields(*(sl.field(name, value) for name, value in rows))`.
- Bare strings are trusted Discord Markdown. Use `md(t"Build {title}")` for escaped Python
  3.14 template-string interpolation, `plain()` for literal text, and `raw_md()` only for a
  deliberately trusted interpolation.
- It contains no translation markers. All user-facing chrome (nav labels, spill lines, page
  footers) enters pre-translated through `Chrome`; build one per locale.
- If it ever grows `_()` markers, the host's Babel config must add
  `[python: packages/squid-layouts/src/**.py]`.
