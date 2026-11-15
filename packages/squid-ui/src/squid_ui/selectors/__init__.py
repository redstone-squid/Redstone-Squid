"""Read-only UI selectors for tests, tooling, and runtime inspection.

Index existing nodes with index_tree(nodes), or preserve component boundaries with
index_tree(render_component_tree(component)). Selectors never render or dispatch actions.
"""

from squid_ui.selectors._query import query
from squid_ui.selectors.core import Query, QueryError, Selector, scope, select
from squid_ui.selectors.parser import SelectorSyntaxError, parse
from squid_ui.selectors.tree import Children, Match, TreeIndex, index_tree, ui_children

__all__ = [
    "Children",
    "Match",
    "Query",
    "QueryError",
    "Selector",
    "SelectorSyntaxError",
    "TreeIndex",
    "index_tree",
    "parse",
    "query",
    "scope",
    "select",
    "ui_children",
]
