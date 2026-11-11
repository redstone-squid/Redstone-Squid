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

For reusable state and behavior without rendering, extend `sl.Reactive`. `Component` already
extends it. A model can contain state, computed properties, resources, operations, and other models.
Pass the same instance to components that should share it; the runtime automatically follows the
fields each render reads. Models need no bus, scope, or `super().__init__()` call. See
[reusable reactive models](https://redstone-squid.github.io/Redstone-Squid/squid-ui-architecture/#reusable-reactive-models).

The base package contains semantic nodes, planning, exact primitives, the component runtime,
the resolved-scene codec, and first-class HTML and Slack planning targets. It has no frontend SDK
dependency; `markdown-it-py` is its direct Markdown parser dependency.

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

For component-owned subscriptions and background work, install `squid-ui[services]`.
`Component.own(ManagedService(...))` declares ownership; a supervised `UIExecutionHost` activates it on
committed mount and awaits cleanup on unmount. Define activity and lexical cleanup in `run()`.
Ordinary state and rendering do not require this optional AnyIO backend. The Discord package
includes and integrates it automatically.

Most Discord applications should install `squid-ui-discord`, which brings this package with it
and provides owner-scoped `Scope`, `Request`, and `Screen` entry points.
Applications compiling Block Kit should install `squid-ui-slack`, which adds Slack SDK drawing
without a listener or delivery runtime.

- [Suite overview](https://redstone-squid.github.io/Redstone-Squid/squid-ui/)
- [Quickstart](https://redstone-squid.github.io/Redstone-Squid/squid-ui-quickstart/)
- [API map](https://redstone-squid.github.io/Redstone-Squid/squid-ui-api/)
- [Architecture and ownership](https://redstone-squid.github.io/Redstone-Squid/squid-ui-architecture/)
- [Scene protocol 1 schema](https://redstone-squid.github.io/Redstone-Squid/schema/scene-v1.schema.json)

## Component lifetime

Create and retain child components during setup or actions. Rendering places those instances
directly: `return self.child` or `sl.stack(self.first, self.second)`. It cannot construct
components or write reactive state. Each instance gets an automatic control namespace that
survives reordering under the same parent. For a namespace stable across new instances or
process restarts, use `child.keyed("editor")` (integer keys are also accepted).

`keyed()` returns an immutable placement reference; it neither modifies the child nor copies
its state. Keys are unique within a parent's render, and one instance can appear only once
in the expanded tree. Replacing a child at the same key mounts the new instance with its own
state. `parent.boundary(child, key="editor")` remains available for compatibility.

Keep dynamic children by an application key and update the collection in an action. The runtime
mounts and unmounts children when the delivered tree changes. Reusing a cached render therefore
preserves the same component identities and lifetimes as running it again.

## Package boundaries

The suite is deliberately split so portable code does not acquire transport or storage imports:

- `squid-reactivity`: transactional state and topic primitives
- `squid-ui`: semantic UI, target planners, runtime, scenes, and native HTML
- `squid-ui-widgets`: reusable frontend-neutral application machines
- `squid-ui-discord`: discord.py rendering, delivery, sessions, and routing
- `squid-ui-slack`: Slack SDK drawing for messages, modals, and App Home
- `squid-storage`: portable persistence contracts and backends
- `squid-replication`: optional replicated-state backends
