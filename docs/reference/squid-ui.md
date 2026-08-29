# squid-ui

The engine: components describe semantic intent, the planner produces an immutable scene for
an explicit target, and a renderer draws that scene without making new layout decisions.
Everything on this page is importable from the package root:

```python
import squid_ui as sl
```

Sections are ordered by when you will need them. The [quickstart](../squid-ui-quickstart.md)
shows the first working screen; the [architecture guide](../squid-ui-architecture.md) explains
the model behind these names.

## Components and state

A component declares reactive fields and renders a layout tree. State changes inside an action
are transactional; `computed` derives values, and `resource` loads asynchronously with
first-class pending and failure states.

::: squid_ui.Component

::: squid_ui.state

::: squid_ui.computed

::: squid_ui.resource

::: squid_ui.ContextKey

::: squid_ui.Document

## Events

Handlers receive one typed event describing what the user did. `PressEvent` and `SubmitEvent`
cover most components; the rest arrive from the matching control kind.

::: squid_ui.PressEvent

::: squid_ui.SubmitEvent

::: squid_ui.ChoiceEvent

::: squid_ui.SelectionEvent

::: squid_ui.EntityEvent

::: squid_ui.EntitySelectionEvent

::: squid_ui.ToggleEvent

::: squid_ui.NavigateEvent

::: squid_ui.OpenEvent

::: squid_ui.ScaleEvent

::: squid_ui.ActionEvent

## Structure factories

Lowercase factories are the authoring vocabulary. Structural factories group content; they say
what a region *is*, and the planner decides what it looks like on each target.

::: squid_ui.section

::: squid_ui.article

::: squid_ui.aside

::: squid_ui.stack

::: squid_ui.block

::: squid_ui.group

::: squid_ui.cluster

::: squid_ui.column

::: squid_ui.columns

::: squid_ui.grid

::: squid_ui.figure

::: squid_ui.details

::: squid_ui.note

::: squid_ui.quote

## Text and display factories

::: squid_ui.heading

::: squid_ui.paragraph

::: squid_ui.md

::: squid_ui.raw_md

::: squid_ui.plain

::: squid_ui.code

::: squid_ui.link

::: squid_ui.timestamp

::: squid_ui.zoned_timestamp

::: squid_ui.status

::: squid_ui.metric

::: squid_ui.progress

::: squid_ui.rating

::: squid_ui.summary

::: squid_ui.item_label

## Collections and tables

::: squid_ui.bullets

::: squid_ui.bullet

::: squid_ui.items

::: squid_ui.item

::: squid_ui.table

::: squid_ui.table_row

::: squid_ui.roster

::: squid_ui.tally

::: squid_ui.media

::: squid_ui.media_item

::: squid_ui.download

## Interactive factories

Controls bind a handler or route to a rendered control. Keys identify a control across
renders; the planner enforces per-target interaction limits.

::: squid_ui.action_control

::: squid_ui.action_controls

::: squid_ui.control_group

::: squid_ui.routed_action_control

::: squid_ui.choice

::: squid_ui.choices

::: squid_ui.routed_choices

::: squid_ui.entity_choice

::: squid_ui.entities

::: squid_ui.toggle

::: squid_ui.form

::: squid_ui.field

::: squid_ui.fields

::: squid_ui.navigation

::: squid_ui.nav_option

::: squid_ui.operation

## Layout control

Combinators that tell the planner how content may adapt when space runs out, and how it is
conditioned or themed.

::: squid_ui.paged

::: squid_ui.budget

::: squid_ui.spill

::: squid_ui.truncate

::: squid_ui.best_effort

::: squid_ui.fallback

::: squid_ui.optional

::: squid_ui.keep_with_next

::: squid_ui.unbreakable

::: squid_ui.controlled

::: squid_ui.uncontrolled

::: squid_ui.themed

## Planning and targets

`planning.plan` turns a document into a `PlanResult` for one explicit target. The four dialect
markers appear in component base classes when a component uses target-specific primitives.

::: squid_ui.planning.plan

::: squid_ui.RenderTarget

::: squid_ui.Renderable

::: squid_ui.DiscordTarget

::: squid_ui.ComponentsV2Target

::: squid_ui.ClassicTarget

::: squid_ui.HtmlTarget

## Native HTML

::: squid_ui.html.target

::: squid_ui.html.Renderer

::: squid_ui.html.DiscordPreviewRenderer

## Theming

::: squid_ui.Palette

::: squid_ui.PaletteRegistry

::: squid_ui.Tone

## Errors

Everything squid-ui raises deliberately derives from `SquidUiError`; planning and drawing
failures form the `LayoutError` family beneath it.

::: squid_ui.SquidUiError

::: squid_ui.LayoutError

::: squid_ui.LayoutInvariantError

::: squid_ui.LayoutDegradedError

::: squid_ui.UnsolvableLayoutError

::: squid_ui.DrawInvariantError

::: squid_ui.ExistingLayoutError

::: squid_ui.LimitViolationError

## Aliases

The types layout factories accept and produce.

::: squid_ui.LayoutNode

::: squid_ui.ChildLike

::: squid_ui.DocumentLike

::: squid_ui.TextLike

::: squid_ui.TextValue

::: squid_ui.Conditional

## Advanced modules

The package root is the supported surface. These namespaces expose advanced composition and
adapter contracts; import from them when the root vocabulary is not enough.

| Module | Purpose |
|---|---|
| `squid_ui.errors` | Typed failures raised at the logical, planning, and drawing boundaries. |
| `squid_ui.forms` | Portable form schemas, typed fields, and descriptor-based form sugar. |
| `squid_ui.guards` | Per-action admission: may *this* press execute right now? |
| `squid_ui.html` | First-class semantic HTML planning and safe scene drawing. |
| `squid_ui.interactions` | Frontend-neutral action events and dispatch metadata. |
| `squid_ui.operations` | Public operation vocabulary supplied by the reactive runtime. |
| `squid_ui.planning` | Portable planning, adaptation, and layout measurement APIs. |
| `squid_ui.primitives` | Exact target-shaped primitives for layouts that require presentation control. |
| `squid_ui.profiling` | Bounded in-process runtime profiling. |
| `squid_ui.resources` | Public resource vocabulary supplied by the reactive runtime. |
| `squid_ui.routing` | Stateless control identity: routes that own their own custom id format. |
| `squid_ui.runtime` | Frontend-neutral component runtime and presentation state. |
| `squid_ui.scene` | Immutable resolved scenes and protocol serialization. |
| `squid_ui.semantic` | Frontend-neutral semantic layout vocabulary. |
| `squid_ui.sources` | Position tokens and asynchronous window loading. |
| `squid_ui.temporal` | Portable exact-time values and local-time resolution. |
| `squid_ui.text` | Resolved text values and safe Discord Markdown interpolation. |
