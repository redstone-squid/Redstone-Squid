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
any component-bearing nodes built from the `sl.discord.PageContext`. Semantic pickers page through keyed
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
other processes; `sl.discord.durability.MountManager` provides opt-in versioned state checkpoints.

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
mount = sl.discord.Mount(tabs.component())

# A restart-surviving message: state is decoded from and encoded into route parameters.
shell = sl.RouterShell(
    lambda request: BUILD_TAB.id(build_id=build.id, tab=request.state.selected),
)
document = shell.render(tabs, sl.TabsState(selected=tab))
```

`PatternRoute.phase` is `next` for deterministic buttons: its state is already the state the next
document should render. Selects and forms use `input`, because their values arrive in the
interaction; a route handler calls `RouterShell.transition(...)` with those values and rebuilds the
whole document. `Wizard.form_for(...)` and `MultiChoicePanel.form_for(...)` return the form a routed
input action should present. The route builder owns compact state encoding and receives the normal
100-character custom-id budget check from `sl.routed_action`/`sl.routed_choices`.

Routed patterns accept frontend-neutral content only. A mounted shell may embed child `Component`
instances, but a process-independent route cannot carry an in-memory component identity.

### Async cursor sources

A ranking too large to materialize may instead use `source=`. A source declares only what it can
prove and fetches one window at a time:

```python
class BuildSource:
    countable = False
    bidirectional = True
    jumpable = False

    async def fetch(self, position: sl.Position, extent: int) -> sl.Window[Build]:
        rows, has_previous, has_more = await builds.window(position, extent)
        return sl.Window(tuple(rows), has_previous, has_more)

ranking = sl.RankedList(
    source=BuildSource(),
    key="builds",
    label=lambda build: build.title,
    value=lambda build: build.score,
    identity=lambda build: str(build.id),
    page_size=10,
).component()
```

`position.direction` is `"around"` for an anchor-preserving refresh, `"forward"` for rows after the
anchor, and `"backward"` for rows before it. Return `Window.position` when an absent anchor makes the
source choose a nearest key, newest row, or start. The component fingerprints only visible identities
and drops a fetch that completes after a newer request. Count and range chrome follows
`countable`/`jumpable`; an uncountable source never exposes the `Window.total` it may accidentally
return. Source-backed rankings require the component shell because fetching stays outside planning and
cannot run in `RouterShell.render()`.

## Host integration rules

- The base package has no dependencies. Install the `discord` extra for discord.py and anyio. The adapter never spawns tasks — start
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
