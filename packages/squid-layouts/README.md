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
        return sl.Section(
            (
                sl.Paragraph(sl.md(t"Count: {self.count}")),
                sl.Actions((sl.Action("increment", "+1", self.increment),), key="counter-actions"),
            ),
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
   `conform(strict=True)`; HTML produces escaped Discord-like preview markup from the same scene.

`compose()` is the Discord convenience pipeline, with `reserved_text` for callers whose
message carries content the engine cannot see. It always creates a renderer-owned view;
adopting an arbitrary existing `discord.py` view is intentionally unsupported. Components nest through explicit
`self.embed(child, key=...)` boundaries, so actions and pagers never cross-wire. `Mount`
binds a component tree to a message: every
interaction funnels through it (author lock, error hook, re-render/edit), timeouts disable
controls, `Reactor` coalesces out-of-band refreshes, and `Navigator` stacks screens with
Back/Home/Close by composition. A mount's `nav=` replaces the stock Previous/Next row with
any component-bearing nodes built from the `PageContext`. Semantic pickers page through keyed
25-option windows. A keyed root `Document` may promote structural overflow to whole-message
pages; local pagination wins, and local plus root navigation are never shown simultaneously.
`render_static` is the sessionless
path for reconciler-managed posts. `build_modal`/`conform_modal` do the same for modals,
whose string lengths discord.py does not validate at all. `SceneCodec` transports plans to
other processes; `MountManager` provides opt-in versioned state checkpoints.

## Host integration rules

- The package depends on discord.py and anyio only, and never spawns tasks — start
  `Reactor.run()` under your own supervisor.
- Bare strings are trusted Discord Markdown. Use `md(t"Build {title}")` for escaped Python
  3.14 template-string interpolation, `plain()` for literal text, and `raw_md()` only for a
  deliberately trusted interpolation.
- It contains no translation markers. All user-facing chrome (nav labels, spill lines, page
  footers) enters pre-translated through `Chrome`; build one per locale.
- If it ever grows `_()` markers, the host's Babel config must add
  `[python: packages/squid-layouts/src/**.py]`.
