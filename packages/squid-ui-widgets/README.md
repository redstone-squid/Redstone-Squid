# squid-ui-widgets

Reusable application machines built on [`squid-ui`](../squid-ui/README.md): the
state machines behind a wizard, a browser, an editor, a lookup, a ranked list, a vote.

`squid-ui` gives you the words and the compiler. This package is a set of useful
applications already written in that language.

This is an alpha release. The Python API may change before 1.0.

```python
import squid_ui as sl
import squid_ui_widgets as sp


async def chosen(event: sp.TransitionEvent[sp.DecisionState]) -> None:
    event.source.finish_on_success()


panel = sp.confirm("Delete this build?", on_confirm=chosen)
```

## What is here

| | |
|---|---|
| `Agreement` | multi-party consent, keyed by actor |
| `Browser` | overview and detail over a resource-backed source |
| `CollectionEditor` | add, remove and reorder a typed collection |
| `Decision`, `confirm` | a one-way choice, and the two-option shell over it |
| `Editor` | sectioned form editing with unsaved-change tracking |
| `SearchPicker` | search a source and pick from it |
| `Menu`, `Tabs` | routed navigation between panels |
| `MultiChoice` | grouped multi-select with a commit policy |
| `RankedList`, `SourceRankedList` | ordered lists, in memory or paged from a source |
| `Wizard` | multi-step flow with a review step |
| `StateMachine`, `ComponentDriver`, `RouteDriver` | the shells the rest are composed from |

## Frontend-neutral, deliberately

Nothing here imports a frontend SDK, `squid-ui-discord`, `squid-ui-slack`, or a store backend — the machines are state
machines that render portable documents, and any target `squid-ui` can plan for can display
them. `tests/architecture/test_boundaries.py::test_patterns_package_is_transport_free` is what
keeps that true.

`squid_ui_widgets.guards.confirm` lives here rather than in `squid_ui.guards` for the same
reason, inverted: the guard *vocabulary* is portable and has no opinion about what a refusal
looks like, but `confirm` answers with a rendered question, so it belongs beside the shell it
renders. It composes with `all_of` and `any_of` like any other guard.

## Mounting Tabs and Wizard

`Tabs` and `Wizard` are components. Construct and retain them once, then return them
directly or include them in a layout container, for example `sl.stack(self.tabs)`.
Child components in tab content retain their own instance namespaces; moving them between
tabs does not assign a new namespace based on the tab or their position.
Pass `on_change` and optional `initial_state` to its constructor. Each instance owns its
selection; `machine_state` is nonpersistent reactive state.

Migration: replace `Tabs(...).build_component(on_change=callback, initial=state)` with
`Tabs(..., on_change=callback, initial_state=state)`. The `initial` constructor argument
still selects the default tab key; an explicit `initial_state` overrides that starting state.

For pure transitions or routed controls, construct `squid_ui_widgets.machines.Tabs` and
use `RouteDriver`. A mounted component exposes its pure definition through read-only `.machine`;
configuration and transition methods are not forwarded onto the component. Definitions
containing child component instances cannot be shared across simultaneous mounts.

Likewise, replace `Wizard(...).build_component(on_finish=callback, initial=state)` with
`Wizard(..., on_finish=callback, initial_state=state)`. The callback receives answers for
the current branch when incomplete state becomes complete; closing remains explicit through
`event.source.finish_on_success()`. Routed hosts use `machines.Wizard` and supply their own
state codec. Other machine-based widgets retain `build_component()` for now.
