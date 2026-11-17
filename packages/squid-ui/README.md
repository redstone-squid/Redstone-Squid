# squid-ui

`squid-ui` builds interfaces from application state. Components describe content and
controls in a Document. The planner chooses a layout that fits a frontend's limits
and records it in an immutable Scene. A renderer constructs the frontend output
without making new layout decisions.

See [how rendering and planning work](https://redstone-squid.github.io/Redstone-Squid/squid-ui-architecture/#end-to-end-flow)
for the complete cycle, compilation, pagination, and caching.

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

The base package contains semantic nodes, shared planning, the component runtime, and
the scene envelope. Native primitives, scene vocabularies, codecs, and renderers belong to
`squid-ui-discord`, `squid-ui-slack`, and `squid-ui-html`. The core has no dependency
on those packages or their SDKs.

Install `squid-ui-html` to plan a portable document as native HTML:

```python
import squid_ui.ext.html

document = sl.section(
    sl.heading("Build details"),
    sl.paragraph("**Compact**, semantic, and accessible."),
)
planned = sl.planning.plan(document, target=squid_ui.ext.html.target())
fragment = squid_ui.ext.html.Renderer().draw(planned.scene, plan=planned)
```

`Renderer(standalone=True)` adds the document shell and neutral responsive CSS. It accepts only
HTML scenes; `squid_ui.ext.discord.preview.DiscordPreviewRenderer` is available when a tool specifically needs to inspect
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
its state. Keys are unique within a parent's render. To place the same instance twice under
one parent, use distinct keys: `sl.stack(editor.keyed("left"), editor.keyed("right"))`.
These placements share component state, loading, and services. Each renders in its own
context with separate control and presentation keys. Mounting happens at the first delivered
placement, and unmounting at the last removal. An instance can belong to only one runtime
at a time; cycles remain invalid. Replacing a child at the same key mounts the new instance
with its own state.

Keep dynamic children by an application key and update the collection in an action. The runtime
mounts and unmounts children when the delivered tree changes. Reusing a cached render therefore
preserves the same component identities and lifetimes as running it again.

## Package boundaries

The suite is deliberately split so portable code does not acquire transport or storage imports:

- `squid-reactivity`: transactional state and topic primitives
- `squid-ui`: semantic UI, shared composition, runtime, and scene vocabulary
- `squid-ui-html`: HTML composition, limits, and rendering
- `squid-ui-widgets`: reusable frontend-neutral application machines
- `squid-ui-discord`: discord.py rendering, delivery, sessions, and routing
- `squid-ui-slack`: Slack SDK drawing for messages, modals, and App Home
- `squid-storage`: portable persistence contracts and backends
- `squid-replication`: optional replicated-state backends

## Querying UI trees

Use `squid_ui.selectors` for read-only queries in tests and inspection tools:

```python
from squid_ui.runtime.expansion import render_component_tree
from squid_ui.selectors import query, select, index_tree
from squid_ui.authoring.semantic import ActionControl

# Rendering is explicit. query accepts existing nodes, scenes, or component trees.
tree = render_component_tree(screen)
save = select(ActionControl).key("save")
match = query(tree, save.qualified_key("editor.save")).one()
assert match.node.available
```

`select(Type)` includes subclasses; `select(instance)` matches that exact object by
identity, including all its placements. Refine it with `key`, `qualified_key`, `attr`,
`has_attr`, `text`, `label`, or a read-only `where` predicate. Text and labels match
exactly by default; `contains=True` or a compiled regular expression opts into
partial matching. Queries accept `localization=...` and otherwise resolve text
against the neutral locale. Text matches a node's own content, not its descendants.

Compose selectors with `descendants`, `children`, `next_sibling`, `following_siblings`,
`has`, `intersect`, `union`, and `excluding`. For example, `select(Editor).key("editor").descendants(save)` selects
save controls inside that component placement. `query(tree, save, within=match)`
searches a matched subtree in a shared tree index, excluding its root unless `include_scope=True` is set.
`scope()` explicitly matches that root. Scoped relations cannot consult ancestors
outside the subtree; `has` evaluates its inner selector relative to each candidate.

Results provide `all()`, `one()`, `optional()`, `exists()`, and `count()`. `one()` and
`optional()` raise `QueryError` on ambiguity, with occurrence paths and keys.
Matches expose `node`, `path`, `owner`, `key`, and `qualified_key`. Repeated objects
are distinct occurrences; unions deduplicate occurrences in depth-first tree order.
Paths for unkeyed nodes are addresses within a tree index, not persistent identities.

`query(tree, selector)` indexes its input automatically. Rendered `ComponentTree`
and planned `Scene` values retain one index, so repeated queries can share scopes
and compare match order directly. A new render gets a new index. For mutable lists,
native views, or custom child adapters, indexing happens on each query; use
`tree = index_tree(nodes)` when several queries should share one topology.
`precedes` uses depth-first order (ancestors precede descendants); sibling selectors
require a shared parent. Queries do not implicitly render a live component.

A `ComponentTree` retains authored boundaries and local keys, including all layout
alternatives before planning. It describes authored UI, not what a frontend finally
shows. Indexing raw nodes inspects exactly those nodes without recovering component
ownership or undoing namespacing. Custom trees can pass a `children` adapter yielding
`(edge_name, child)` pairs. Traversal follows declared UI edges, never arbitrary
application records or callbacks, and rejects ancestor cycles.

A `TreeIndex` retains node references and fixed topology. Type and key indexes narrow
candidate searches, but queries reevaluate attributes, predicates, and localized
text every time. Match results are not cached. Parsed selector strings use a bounded
cache keyed by their source and type registry.

For tests, `squid_ui.testing.render_index(component)` combines explicit rendering
and indexing. Existing `find` and `find_all` keep their general dataclass traversal
and assertion behavior.

Pass a CSS-inspired string directly to `query`, or compile it with `parse` for reuse:

```python
from squid_ui.selectors import parse

save = parse('Editor[key="editor"] ActionControl[key="save"]', types={"Editor": Editor})
query(tree, save).one()
query(tree, "ActionControls:has(> ActionControl[available=false])").all()
query(tree, 'Editor[key="editor"]', types={"Editor": Editor}).one()
```

String queries return `Match[object]`; typed selectors preserve their inferred node type.
Semantic type names and qualified planned-scene names such as `scene.Button` and
`scene.HtmlElement` are built in. Planned Discord, HTML, and Slack scene trees use
their declared display edges, including controls and options. Attributes, action
references, assets, pager records, and extension payloads remain metadata. Register
component or primitive names explicitly with `types={...}`; parsing never imports or evaluates names. Supported syntax is
`*`, types, `[attribute]`, `[attribute=value]`, string prefix `^=`, suffix `$=`,
and substring `*=`, descendant whitespace, `>`, adjacent sibling `+`, subsequent
sibling `~`, comma unions, `:scope`, `:is(...)`, `:not(...)`, and
`:has(...)`. Values use JSON literals, including double-quoted strings, booleans,
numbers, and `null`. Malformed or unsupported syntax raises `SelectorSyntaxError`
with a source offset. CSS IDs, classes, escaping, and positional pseudo-classes
are outside this subset.

Typed string filters are `attr_starts_with`, `attr_ends_with`, and `attr_contains`.
Missing or non-string values do not match. Attribute paths follow explicit dotted
attributes, so `select(Lines).attr("overflow.key", "results")` selects the owner of
a pagination policy without turning that policy into a UI child. The string form
is `Lines[overflow.key="results"]` with `types={"Lines": Lines}`.

Native Discord UI queries live in `squid_ui_discord.testing`:

```python
from squid_ui_discord import testing as discord_testing

save = discord_testing.query(view, 'Button[label="Save"]').one()
# A staged harness inspects its latest delivered views, without rendering again.
save = staged.query('Button[label="Save"]').one()
```

These inspect native Discord items and their native attributes (`custom_id`,
`disabled`, etc.), not authored controls or reconstructed semantic keys. Nested
containers, section accessories, select options, and modal inputs are supported.
Use `discord_testing.index_tree(view)` or `staged.index()` for shared scopes or
ordering. `staged.query(selector, view=candidate)` explicitly inspects a candidate
view; the default keeps pending renders separate from committed output.

Framework wording is supplied through `Chrome`. Its defaults are English; applications
own translated labels and callbacks, including their catalogs. Pass plain localized
strings or deferred `TextLike` values to override them. Deferred application text is
still resolved with the render or session localization.

Standalone helpers accept `chrome=`, including `tally`, `page_jump_form`, and
`FormSpec.evaluate`. A field can also carry its own `chrome` for direct parsing.
Mounted form submissions use the mount's chrome. Framework packages ship no
translation catalogs; applications can use any localization system to supply wording.
