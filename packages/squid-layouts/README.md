# squid-layouts

A declarative, limits-aware UI engine with Discord Components V2, classic-message, and HTML renderers.

Discord rejects any message that exceeds one of its many hard limits (4000 display characters,
40 components, 25 select options, 45-char modal titles, …) with an opaque HTTP 50035. This
package prevents known target-limit violations before delivery: views describe *intent*, and the engine measures
every markdown prefix and code fence exactly, allocates the shared budgets by priority, and
degrades content the way its author said it should degrade.

```python
import squid_layouts as sl

class Counter(sl.Component):
    count: int = sl.state(0)

    def render(self):
        return sl.section(
            sl.heading("Counter"),
            t"Count: {self.count}",
            sl.actions(sl.action("+1", self.increment, key="increment"), key="counter-actions"),
        )

    async def increment(self, event: sl.PressEvent) -> None:
        self.count += 1  # the mount re-renders and edits the message
```

See [the architecture and API interaction guide](../../docs/squid-layouts-architecture.md)
for component composition, planning, renderers, action policies, and durable mounts, and
[Classic Discord messages](docs/classic-messages.md) for the second target: content, embeds,
and action rows, from the same semantic document.

## The layers

1. **Semantic documents** describe author intent with `Section`, `Paragraph`, `List`,
   `Fields`, `Table`, `Media`, `Details`, `Actions`, `Choices`, `Items`, and `Navigation`.
   Authors may express a display preference and flexibility, but never an exact Discord shape.
   The lowercase factories (`sl.section`, `sl.actions`, `sl.field`, …) are the recommended
   authoring surface; the uppercase dataclasses are the IR they build and stay fully public.
2. **Target adapters** select lossless representations, per target: one document becomes a
   `LayoutView` under `V2_TARGET` and content, embeds, and action rows under `CLASSIC_TARGET`.
   For example, 36 semantic actions become
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

`sl.discord.compose()` is the Discord convenience pipeline, with `reservation` for callers whose
message carries content the engine cannot see — `sl.discord.measure(view)` and `sl.discord.cost(item)`
produce one without hand-counting. It always creates a renderer-owned view;
adopting a *live* `discord.py` view — one already sent, which will edit its own message — is
intentionally unsupported, because two writers on one message make measurement unsound.

There are four ways to adopt the package, and they can be mixed in one bot:

1. **A new screen.** Use `sl.discord.Mount` for one command while everything else stays as it is.
2. **A region of an existing V2 screen.** `sl.discord.contribute(document, to=view, followed_by=...)`
   measures the host, plans into what is left, and places the result — the host keeps sending,
   editing, timeouts, callbacks and error policy. The contributed region is stateless: link and
   routed controls only, since no mount exists to wire a component-local callback.
3. **An unsent classic view.** `sl.discord.adopt(view)` turns a never-sent `discord.ui.View`
   into a `Component`: Squid builds its own controls from `view.children`, owns the message,
   and dispatches to the legacy callbacks unchanged. The mount owns the timeout, and anything
   that would make the legacy object a second writer raises `AdoptionError`.
4. **The whole message.** Hand it to `Mount` when component state or callbacks move into Squid.

See [Migrating an existing discord.py bot](docs/migrating.md) for an incremental path covering
V2 and classic contributions, persistent routes, mounts, sessions, forms, and durability.

A fragment is not a miniature mount. If two independently stateful regions need to edit one
message, give them a single parent component, or keep the legacy view as the sole owner and make
the Squid region stateless. Components nest through explicit
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

Presentation colours are an immutable `sl.Palette`, supplied to `sl.planning.plan`, `sl.discord.compose`,
`sl.discord.render_static`, or `sl.discord.Mount`. An omitted section or article accent inherits
`Palette.brand`; `accent=None` explicitly opts out and an integer remains an exact data override.
Semantic tones resolve through the palette. `sl.themed(palette, *children)` scopes an override to a
subtree, so a component may select a palette from reactive state while the final scene still contains
only exact colours. Discord buttons retain Discord's platform-owned style colours.

### Discord component parity

Discord modals may interleave static text and fields in declaration order. Native checkbox groups
remain Discord-specific and declare their portable fallback explicitly:

```python
choices = (
    sl.forms.ChoiceOption("alerts", "Alerts", "alerts", emoji="🔔"),
    sl.forms.ChoiceOption("reports", "Reports", "reports"),
)
field = sl.discord.CheckboxGroupField(
    key="subscriptions",
    label="Subscriptions",
    options=choices,
    required=False,
    fallback=sl.forms.MultiChoiceField(options=choices, required=False),
)
form = sl.forms.FormSpec(
    "Preferences",
    (sl.forms.FormText("Choose every update you want to receive."), field),
)
```

The exact primitive API exposes Discord-only controls and complete media metadata. A premium
button must have a positive SKU and no interactive or link fields; link and interactive buttons
may omit their label when they have an emoji. Custom emoji use `sl.emoji.Emoji(name, id, animated=...)`.

```python
from squid_layouts.emoji import Emoji
from squid_layouts.primitives import Gallery, GalleryItem, LinkButton, PremiumButton, Row

document = (
    Row(
        (
            PremiumButton(sku_id=123456789),
            LinkButton(None, "https://example.com", emoji=Emoji("docs", 987654321), disabled=True),
        )
    ),
    Gallery((GalleryItem("https://example.com/preview.png", "Build preview", spoiler=True),)),
)
```

Premium controls are supported by both Discord message targets. Other targets require a
`Variants` rung with an explicit fallback. Components V2 preserves media descriptions and spoiler
state; classic Discord and other targets reject V2-only media structures rather than silently
discarding that content.

### Live updates across mounts

`TopicBus` is the two-method protocol for a payload-free latency projection. Publishing says only
that an address changed; every subscriber re-reads the application's source of truth.
`LocalTopicBus` is the synchronous in-process implementation for tests and single-process hosts.

An address is either a `sl.runtime.Topic(kind, key)` -- a value a host writes, equal by value so two
publishers agree without sharing a constructor -- or a `sl.runtime.CellAddress`, which is a `Shared` cell's
identity and is only ever received, never built. `sl.runtime.Address` is the union the bus carries. Keys
are text on purpose: `sl.runtime.Topic("build", 123)` is a type error rather than a topic nobody else ever
addresses.

Watch a topic where you read the thing it names, and the mount follows it for you:

```python
class BuildPanel(sl.Component):
    def __init__(self, build_id: str) -> None:
        self.build_id = build_id

    @sl.resource(pending=sl.resources.PendingPolicy.ATOMIC)
    async def build(self) -> Build:
        sl.runtime.watch(sl.runtime.Topic("build", self.build_id))
        return await queries.get_build(self.build_id)

    def render(self):
        return sl.Text(self.build.value.name)
```

`sl.runtime.watch` is a tracked read like any other, so the render that used the resource's value
follows the topic, a render that stops reading it stops following, and `bus.publish` re-pends
the resource before the mount redraws. Nothing is subscribed by hand, so nothing has to be
unsubscribed -- and the initial load is just the resource's first settle. Prefer
`PendingPolicy.ATOMIC` for live data: the default `EXPLICIT` would flash a pending paint on
every external change.

Because the topic carries a version, a publish landing *during* the load is not lost: it moves
what the load is being compared against, so the value it produced is already stale and settles
again. There is no "subscribe before the first read" rule to get wrong. `sl.runtime.watch` belongs in a
resource, never in `on_load`, which runs once and under no consumer.

`reactor.follow` remains for a dependency no render-time read can express, and `bus.subscribe`
for a subscriber that is not a mount:

```python
bus = sl.runtime.LocalTopicBus()
reactor = sl.discord.Reactor(bus)
mount = sl.discord.Mount(panel, access=sl.discord.Owner(interaction.user.id), scheduler=reactor)
reactor.follow(mount, sl.runtime.Topic("build", "123"))  # subscribe before the first read/send
await mount.send(sl.discord.respond_to(interaction))

# The host owns the only long-running coroutine.
async with anyio.create_task_group() as tasks:
    tasks.start_soon(reactor.run)
```

`publish()` delivers local subscribers synchronously in registration order. A subscriber that
raises is reported through the bus's error hook, delivery continues to the rest, and the bus has
no task that can die. A distributed application bridges
its own durable change feed, NOTIFY listener, or queue consumer into the local bus. Publish where
the application already funnels committed changes; do not subscribe a durable projection that
already has a reconciler, because that would give one message two competing writers. For tests,
call `publish()` and assert immediately.
Expiry and idle-time tests can inject UTC and monotonic clocks through `Reactor(clock=...)` and
`Mount(clock=...)`; production callers normally keep their defaults.

When the change happens in another process -- a worker that finished a render, a second shard --
the host bridges it in. `PostgresTopicBridge` is that bridge for a deployment that already runs
PostgreSQL: it publishes through the bus rather than relaying it, so nothing loops, and the
payload is an encoded *address*, never state.

```python
bridge = sl.discord.durability.PostgresTopicBridge(pool, bus)
tasks.start_soon(bridge.run)   # LISTEN, plus the outbound sender
bridge.publish(sl.runtime.Topic("build", "123"))  # local subscribers now, other processes shortly after
```

The default wire form is `sl.runtime.KindKeyCodec`, which writes `kind:key` and is total on `Topic`, so
most hosts pass no codec at all. A `CellAddress` cannot reach a codec -- it names a live object
rather than a value -- so shared cells stay process-local by type rather than by convention. Pass
`codec=` only to speak a format someone else already defined:

```python
bridge = sl.discord.durability.PostgresTopicBridge(pool, bus, LegacyCodec())
```

Every process that takes part runs the bridge, including one that only publishes. Delivery is
exactly as durable as the bus: NOTIFY reaches whoever is listening at commit time, so a restart
or a dropped connection costs latency, not correctness -- keep the reconciler or poll that already
makes the projection converge. A reconnect calls the optional `on_resync()` for hosts that want to
republish their coarse topics; payloads must stay under 8000 bytes.

`publish()` is immediate on this process and queues a remote notification. When the change belongs
inside an application-owned PostgreSQL transaction, use `publish_in()` instead:

```python
async with connection.transaction():
    await save_change(connection)
    await bridge.publish_in(connection, sl.runtime.Topic("build", "123"))
```

`publish_in()` does not commit the transaction. PostgreSQL holds its notification until commit,
then the bridge listener publishes it locally and in other processes. The bridge must be running
before calling it; unlike `publish()`, it rejects topics that cannot be encoded or fit under the
NOTIFY payload limit.

Every delivered mount using a reactor is observed for edit-authority expiry, even when it follows
no topics. `PauseUpdates(warning=60)` is the default pre-expiry status policy. A long-lived
ephemeral panel can opt into an explicit, non-mutating handoff instead:

```python
mount = sl.discord.Mount(
    panel,
    access=sl.discord.Owner(user_id),
    scheduler=reactor,
    expiry=sl.discord.RenewEphemeral(warning=90),
)
```

The renewal screen preserves the mount and hidden application state, then restores the latest
render on the same message when its owner clicks **Continue Session**. Pass `expiry=None` to
disable pre-expiry UI. `RenewEphemeral` requires a reactor-backed scheduler so timed arming cannot
silently be missed.

### Runtime profiling

Runtime profiling is opt-in, bounded, and synchronous. One `MemoryProfiler` can cover the host's
live-update chain: pass it to `Reactor`, and a `Mount` inherits its scheduler's profiler unless
explicitly overridden. Routers are independent ownership roots and accept the same collector
directly.

```python
profiler = sl.profiling.MemoryProfiler(sample_rate=0.1)
bus = sl.runtime.LocalTopicBus()
reactor = sl.discord.Reactor(bus, profiler=profiler)

mount = sl.discord.Mount(panel, access=sl.discord.Everyone(), scheduler=reactor)
router = sl.discord.Router(profiler=profiler)
devtools = sl.discord.devtools.DevTools(reactor=reactor)
```

Completed operations contribute to lifetime and rolling histograms and event counters even when
their detailed trace is sampled out. Slow, failed, cancelled, and acknowledgement-deadline traces
have dedicated bounded retention. `profiler.snapshot()` returns frozen data, and
`sl.profiling.snapshot_json(snapshot)` exports schema-versioned JSON without consulting live
objects. The profiler starts no tasks and performs no I/O; keep exporters under the host's own
supervisor.

The owner-only devtools cog adds `dev profile actions`, `dev profile queues`,
`dev ui profile <mount-id>`, and `dev profile export`. Trace attributes may retain a mount ID in
these bounded buffers, but mount IDs, topics, users, message IDs, route values, and form payloads
never become aggregate keys.

## Interaction patterns and two shells

`Tabs`, `Menu`, materialized `RankedList`, `Wizard`, and `MultiChoicePanel` are pure state machines.
They do not choose between in-memory callbacks and restart-surviving routes. Instead, a shell injects
that control construction:

```python
tabs = sl.patterns.Tabs(
    (sl.patterns.Tab("summary", "Summary", summary), sl.patterns.Tab("history", "History", history)),
    key="build-tabs",
)

# A mounted message: state lives in sl.state and controls use sl.action closures.
mount = sl.discord.Mount(tabs.component(), access=sl.discord.Everyone())

# A restart-surviving message: state is decoded from and encoded into route parameters.
shell = sl.patterns.RouterShell(
    lambda request: BUILD_TAB.id(build_id=build.id, tab=request.state.selected),
)
document = shell.render(tabs, sl.patterns.TabsState(selected=tab))
```

Mounted actions accept application-wide middleware directly on the mount:

```python
class TraceActions(sl.interactions.ActionMiddleware):
    async def dispatch(self, request, proceed) -> None:
        with tracer.span("ui.action", action=request.key, rebased=request.rebased):
            await proceed()


mount = sl.discord.Mount(
    panel,
    access=sl.discord.Everyone(),
    middleware=(TraceActions(),),
)
```

The first middleware is outermost. Returning without `proceed()` short-circuits the handler but
still lets the mount acknowledge and flush; `proceed()` is one-shot and expires when that
middleware call returns. Middleware runs after mount admission and stale-generation handling.
Its `ActionRequest.rebased` flag is generation metadata, not a completion result. Handler state
rolls back before an exception reaches outer middleware, and Discord rendering/delivery remains
outside the onion. The onion itself is outside the handler transaction so it can catch commit-time
shared-state conflicts. Component state written by middleware is consequently independent and does
not roll back with handler state; middleware is a policy surface unless that independence is
intentional.

`Mount.on_committed()` and `Mount.on_presented()` observers are synchronous. Committed observers run
whenever application runtime state advances, including when an identical presentation suppresses the
Discord edit. Presented observers run only at delivered-generation commit points. Both run under the
mount's render lock and must not await or call lock-taking mount operations. External hosts migrating
an async observer should enqueue its work or start it through an owned task supervisor.

Every mount states who may interact. `Owner`, `Users`, and `Everyone` cover static policy;
`Check` accepts an asynchronous application policy. Visibility stays a destination concern,
so owner access does not by itself make a channel message private.

`SessionRegistry` groups a root mount and its attached children under one operational lifetime.
Typed `SessionKey.user`, `guild`, `user_guild`, `global_`, and `custom` constructors name scoped
cardinality, while `SessionPolicy` composes a limit, collision selection, and replacement
protection. Opens return `Opened`, `Rejected`, or `Abandoned`; no preflight `get()` is needed to
explain a collision.

`Screen` holds reusable key scope, admission, access, and mount policy for a logical application
screen. Use `screen.respond(sessions, component, interaction)` when one interaction supplies both
delivery and opener identity; use `screen.open(...)` with an explicit `Destination` and `Opener`
for other transports.

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

### State values

`sl.state` holds a value that is replaced, never mutated in place. The type checker enforces
it: a `dict`, `list` or `set` default declares the field as `Mapping`, `Sequence` or
`AbstractSet`, so `self.rows.append(x)` and `rows: list[str] = sl.state([])` are both type
errors, while the stored value is exactly the one assigned. `{**m, k: v}` replaces a key; for more
than that, copy into a local and assign it back.
`sl.state(..., opaque=True)` declares a collaborator the component holds and never mutates — a
service, a guild, a session — which settles on identity and is never persisted.

Inside an action a write stages into the transaction's overlay and becomes visible at commit.
The action reads its own writes; another task reading the same field across an `await` sees the
committed value until then, and a rollback is dropping the overlay.

### Action history

History is opt-in and component-owned. Declare a bounded stack like state, then pass it to
state-changing actions; the framework records the whole state delta only when the action commits:

```python
class Editor(sl.Component):
    history: sl.runtime.History = sl.runtime.history(limit=20)
    title: str = sl.state("")

    def render(self):
        return sl.actions(
            sl.action("Rename", self.rename, key="rename", record=self.history),
            sl.runtime.history_actions(self.history),
            key="editor-actions",
        )

    async def rename(self, event: sl.PressEvent) -> None:
        self.title = "New title"
```

For an action that also changes a database or API, call `self.history.record("Rename", undo=..., redo=...)`
inside the handler. The framework restores component state; the supplied async callbacks reverse
external work. A failed action creates no entry, and a new recorded action clears redo history.

### Shared state

`sl.runtime.Shared` is a namespace of view state several live mounts agree on. Subclass it, declare
state with `sl.state()`, and hand the same instance to whoever should see the same values:

```python
class Workspace(sl.runtime.Shared[GuildId]):
    selected: int | None = sl.state(None)
    filters: tuple[str, ...] = sl.state(())

workspace = Workspace(bus, guild.id)
```

It is the same `sl.state()` a component declares, and literally the same storage, so
replacement, `opaque=`, staging and rollback are identical. What differs is who is told when it
changes, and that is decided by what holds it rather than by how it is declared: a component's
state invalidates its one owner, while a namespace's publishes an address on the `TopicBus`.
Every mount whose render read that field refreshes; a mount subscribes to exactly what it
rendered, reconciled each time it stages one. The mount that *made* the write repaints in the
click itself rather than waiting for the bus, so a panel writing shared state feels no
different from one writing local state — and needs no reactor to do it. A field an action both read and
wrote carries the value it read as a commit precondition, so a lost update raises
`sl.runtime.SharedStateConflictError` rather than overwriting — derived from what the handler did, with
no `compare_and_set` to remember. `sl.runtime.history` covers shared writes in the same entry as local
ones and restores them blindly.

There is no store: two panels converge because something gave them the same object, so the
handle is the state and its lifetime is whoever holds it. Keep domain truth in your data layer;
a namespace is for what only the screen wants.

### Computed values

`sl.computed` caches a synchronous derived value against whatever state its body read. Nothing
is declared, so a conditional dependency is exactly the branch that ran:

```python
class SearchResults(sl.Component):
    query: str = sl.state("")
    filters: frozenset[str] = sl.state(frozenset())

    @sl.computed
    def visible_results(self) -> tuple[Result, ...]:
        return apply_filters(search(self.query), self.filters)
```

A computed may read another component's state, or another computed. It is lazy — one nobody
renders is never evaluated — and one that raises fails where its value is used rather than at
commit. Values form selector boundaries: downstream computed values recompute only when the
refreshed value compares unequal. `sl.runtime.untracked()` reads state without subscribing to it.

### Reactive async resources

`sl.resource` keeps async data in a synchronous, renderable state machine. Its dependencies are
whatever its loader read, tracked the same way a computed's are:

```python
class VotingPanel(sl.Component):
    kind: VoteKind = sl.state(VoteKind.BUILD)

    @sl.resource
    async def configuration(self) -> VoteConfiguration:
        return await votes.configuration(self.kind)

    def render(self):
        match self.configuration.status:
            case sl.resources.Pending(previous=None):
                return sl.note("Loading…")
            case sl.resources.Pending(previous=sl.resources.Ready(value=config)):
                return refreshing_panel(config)
            case sl.resources.Failed(error=error, previous=previous):
                return failed_panel(error, previous)
            case sl.resources.Ready(value=config):
                return voting_panel(config)
```

A committed write to state the loader read re-pends the resource at the next read. A loader
that reads something only in one branch must hoist that read, or a run that skipped the branch
will not subscribe to it. Hidden branches remain lazy:
only resources observed during rendering are loaded. `Pending` and `Failed` retain the last `Ready`
value when available, while request tokens prevent stale completions from publishing.

Explicit pending is the default: Discord commits the pending render, settles observed sibling
resources concurrently, then edits to the settled render. Use
`@sl.resource(pending=sl.resources.PendingPolicy.ATOMIC)` when pending should remain internal and
the first delivery must already be settled. Its `.status` is typed as `Ready[T] | Failed[T]`:
refreshes expose the previous `Ready` value while loading, and an initial pending read is retried
by the mount. Both policies use the same internal state machine and neither starts detached
background work.

`await panel.configuration.reload()` is the caller-owned, awaited form. `invalidate()` requests a
new value without immediately loading it, and `replace(value)` publishes an authoritative local
result. Resource state is runtime-only and is not included in durable component snapshots.

### One-shot operations

`sl.operation` is a component-bound effect that runs once under the mount's caller-owned task.
Progress is explicit, reactive current state; it is not an event stream:

```python
class PublishVote(sl.Component):
    @sl.operation(initial=PublishProgress.CREATING)
    async def publication(
        self,
        progress: sl.operations.Progress[PublishProgress],
    ) -> VoteId:
        progress.set(PublishProgress.PUBLISHING)
        return await votes.publish()

    def render(self):
        match self.publication.status:
            case sl.operations.Pending(progress=progress):
                return pending_vote(progress)
            case sl.operations.Succeeded(value=vote_id):
                return published_vote(vote_id)
            case sl.operations.Failed(error=error, progress=progress):
                return failed_vote(error, progress)
            case sl.operations.Cancelled(progress=progress):
                return cancelled_vote(progress)
```

Unlike a resource, an operation has no `reload`, `invalidate`, or `replace`: success, failure,
and cancellation are terminal. Mounts coalesce progress invalidations through their ordinary
stage/deliver/commit path while the operation runs. Resources remain repeatable and intentionally
have no cancelled status; cancelling one load attempt leaves it pending and retryable.

### Async cursor sources

A ranking too large to materialize uses the distinct `SourceRankedList` component. A source declares
only what it can prove and fetches one window at a time:

```python
class BuildSource:
    capabilities = sl.sources.SourceCapabilities(backward=True)

    async def fetch(self, position: sl.sources.Position, extent: int) -> sl.sources.Window[Build]:
        resolved, rows, has_previous, has_more = await builds.window(position, extent)
        return sl.sources.Window(resolved, tuple(rows), has_previous, has_more)

ranking = sl.patterns.SourceRankedList(
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

- The base package depends only on the zero-dependency `squid-reactive` kernel. Install the
  `discord` extra for discord.py and anyio. The adapter never starts background work on its own;
  start `sl.discord.Reactor.run()` and any external bridge under your own supervisor.
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
