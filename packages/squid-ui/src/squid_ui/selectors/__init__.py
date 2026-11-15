"""Read-only UI selectors for tests, tooling, and runtime inspection.

Capture existing nodes with snapshot(nodes), or preserve component boundaries with
snapshot(render_component_tree(component)). Selectors never render or dispatch actions.
"""

from squid_ui.selectors.core import Query, QueryError, Selector, query, scope, select
from squid_ui.selectors.tree import Children, Match, Snapshot, snapshot, ui_children

__all__ = [
    "Children",
    "Match",
    "Query",
    "QueryError",
    "Selector",
    "Snapshot",
    "query",
    "scope",
    "select",
    "snapshot",
    "ui_children",
]
