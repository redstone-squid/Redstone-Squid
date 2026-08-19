# squid-layouts

A declarative, limits-aware UI framework for Discord Components V2, built on discord.py.

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
        return [
            sl.Heading("Counter"),
            sl.Text(f"count: {self.count}"),
            sl.Row((sl.Button(label="+1", on_click=self.increment, key="inc"),)),
        ]

    async def increment(self, interaction) -> None:
        self.count += 1  # the mount re-renders and edits the message
```

## The layers

1. **Presets** (`card`, `listing`, `report`, `banner`) — common shapes, string-in/string-out.
2. **IR nodes** (`Text`, `Heading`, `Code`, `Lines`, `Section`, `Panel`, `SelectMenu`, …) —
   Discord-shaped, carrying **overflow policies instead of sizes**:
   - `Truncate(keep="head"|"tail")` — ellipsis trim;
   - `Spill()` — show the entries that fit plus "…and N more";
   - `Paginate(initial="start"|"end", per=None)` — split into pages, on overflow or every
     `per` entries; the solver adds a budget-charged footer and the nav factory's controls;
   - `alts(...)` / `Alt(primary, fallbacks)` — degradation ladders: semantically smaller
     alternates beat a mid-string ellipsis;
   - `Drop()`, `Never()` — omit whole, or treat shrinking as a bug.
3. **Solver** — measures chrome, charges `Never` nodes as fixed costs, grants the display
   budget by priority (proportionally within a tier), refunds dropped nodes, applies
   policies only on overflow, and realizes the page controls a `NavFactory` describes.
4. **`conform()`** — the boundary gate: a final walk of the built view that clamps anything
   the solver missed. Tests treat any intervention as a failure (`assert_within_limits`);
   production degrades to an ugly-but-delivered message.

`compose()` is the one pipeline through all four, with `reserved_text` for callers whose
message carries content the engine cannot see. Components nest: `self.embed(child, key=...)`
renders a child into its parent's document under a key prefix, so two instances of the same
child class never cross-wire, and a child's state change re-renders the message through its
parent. `Mount` binds a component to a message: every
interaction funnels through it (author lock, error hook, re-render/edit), timeouts disable
controls, `Reactor` coalesces out-of-band refreshes, and `Navigator` stacks screens with
Back/Home/Close by composition. A mount's `nav=` replaces the stock Previous/Next row with
any component-bearing nodes built from the `PageContext`. `render_static` is the sessionless
path for reconciler-managed posts. `build_modal`/`conform_modal` do the same for modals,
whose string lengths discord.py does not validate at all.

## Host integration rules

- The package depends on discord.py and anyio only, and never spawns tasks — start
  `Reactor.run()` under your own supervisor.
- It contains no translation markers. All user-facing chrome (nav labels, spill lines, page
  footers) enters pre-translated through `Chrome`; build one per locale.
- If it ever grows `_()` markers, the host's Babel config must add
  `[python: packages/squid-layouts/src/**.py]`.
