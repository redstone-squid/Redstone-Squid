# squid-ui

`squid-ui` is a frontend-neutral, limits-aware UI engine. Components describe semantic intent;
the planner turns that intent into an immutable scene that a renderer can draw without making
new layout decisions.

This is an alpha release. The scene protocol is versioned, but the Python API may change before
1.0.

```python
import squid_ui as sl


class Counter(sl.Component):
    count: int = sl.state(0)

    def render(self) -> sl.LayoutNode:
        return sl.section(
            sl.heading("Counter"),
            sl.paragraph(str(self.count)),
            sl.action_controls(
                sl.action_control("Add", self.add, key="add"),
                key="counter-actions",
            ),
        )

    async def add(self, event: sl.PressEvent) -> None:
        self.count += 1
```

The base package contains semantic nodes, planning, exact primitives, the component runtime,
the resolved-scene codec, and a first-class HTML target. It has no Discord client dependency;
`markdown-it-py` is its direct Markdown parser dependency.

Plan a portable document directly to native HTML:

```python
document = sl.section(
    sl.heading("Build details"),
    sl.paragraph("**Compact**, semantic, and accessible."),
)
planned = sl.planning.plan(document, target=sl.html.target())
fragment = sl.html.Renderer().draw(planned.scene, plan=planned)
```

`Renderer(standalone=True)` adds the document shell and neutral responsive CSS. It accepts only
HTML scenes; `DiscordPreviewRenderer` remains available when a tool specifically needs to inspect
a planned Discord Components V2 scene in a browser.

Install the package directly when you are writing a renderer or portable component library:

```console
pip install squid-ui==0.1.0a1
```

Most Discord applications should install `squid-ui-discord`, which brings this package with it
and provides the `Invocation` and `Screen` entry points.

- [Suite overview](https://redstone-squid.github.io/Redstone-Squid/squid-ui/)
- [Quickstart](https://redstone-squid.github.io/Redstone-Squid/squid-ui-quickstart/)
- [API map](https://redstone-squid.github.io/Redstone-Squid/squid-ui-api/)
- [Architecture and ownership](https://redstone-squid.github.io/Redstone-Squid/squid-ui-architecture/)
- [Scene protocol 1 schema](https://redstone-squid.github.io/Redstone-Squid/schema/scene-v1.schema.json)

## Package boundaries

The suite is deliberately split so portable code does not acquire transport or storage imports:

- `squid-reactivity`: transactional state and topic primitives
- `squid-ui`: semantic UI, target planners, runtime, scenes, and native HTML
- `squid-ui-widgets`: reusable frontend-neutral application machines
- `squid-ui-discord`: discord.py rendering, delivery, sessions, and routing
- `squid-storage`: portable persistence contracts and backends
- `squid-replication`: optional replicated-state backends
