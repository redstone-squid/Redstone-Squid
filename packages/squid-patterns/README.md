# squid-patterns

Reusable application patterns built on [`squid-ui`](../squid-ui/README.md): the
state machines behind a wizard, a browser, an editor, a lookup, a ranked list, a vote.

`squid-ui` gives you the words and the compiler. This package is a set of useful
applications already written in that language.

```python
import squid_ui as sl
import squid_patterns as sp


async def chosen(event: sp.PatternEvent[sp.DecisionState]) -> None:
    await event.source.finish()


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
| `Lookup` | search a source and pick from it |
| `Menu`, `Tabs` | routed navigation between panels |
| `MultiChoicePanel` | grouped multi-select with a commit policy |
| `RankedList`, `SourceRankedList` | ordered lists, in memory or paged from a source |
| `Wizard` | multi-step flow with a review step |
| `Pattern`, `ComponentShell`, `RouterShell` | the shells the rest are composed from |

## Frontend-neutral, deliberately

Nothing here imports `discord.py`, `squid-discord`, or a store backend — the patterns are state
machines that render portable documents, and any target `squid-ui` can plan for can display
them. `tests/architecture/test_boundaries.py::test_patterns_package_is_transport_free` is what
keeps that true.

`squid_patterns.guards.confirm` lives here rather than in `squid_ui.guards` for the same
reason, inverted: the guard *vocabulary* is portable and has no opinion about what a refusal
looks like, but `confirm` answers with a rendered question, so it belongs beside the shell it
renders. It composes with `all_of` and `any_of` like any other guard.
