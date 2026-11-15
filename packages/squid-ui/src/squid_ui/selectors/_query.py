"""Evaluate typed and string selectors against an existing tree index."""

from collections.abc import Mapping
from typing import Any, cast, overload

from squid_ui.content.text import NEUTRAL, Localization
from squid_ui.selectors.core import Query, Selector
from squid_ui.selectors.parser import parse
from squid_ui.selectors.tree import Children, Match, TreeIndex, index_tree, ui_children


@overload
def query[T](
    tree: object,
    selector: Selector[T],
    *,
    children: Children | None = None,
    within: Match[Any] | None = None,
    include_scope: bool = False,
    localization: Localization = NEUTRAL,
) -> Query[T]: ...


@overload
def query(
    tree: object,
    selector: str,
    *,
    types: Mapping[str, type] | None = None,
    children: Children | None = None,
    within: Match[Any] | None = None,
    include_scope: bool = False,
    localization: Localization = NEUTRAL,
) -> Query[object]: ...


def query[T](
    tree: object,
    selector: Selector[T] | str,
    *,
    types: Mapping[str, type] | None = None,
    children: Children | None = None,
    within: Match[Any] | None = None,
    include_scope: bool = False,
    localization: Localization = NEUTRAL,
) -> Query[T] | Query[object]:
    """Evaluate once in depth-first order, deduplicating by occurrence, not object identity.

    ComponentTree and Scene reuse their own structural index across queries. Pass a
    TreeIndex to share scopes or ordering when inspecting raw nodes or mutable views.
    Live components require explicit rendering; custom trees can supply children.

    Strings use parse, including its semantic types and optional types registry.
    Typed selectors retain their result type; strings return Match[object] values.

    An unscoped query includes every root. A scoped query includes strict descendants
    unless include_scope is set. Ancestor and has predicates cannot escape the scope.
    """
    if isinstance(selector, str):
        return query(
            tree,
            parse(selector, types=types),
            children=children,
            within=within,
            include_scope=include_scope,
            localization=localization,
        )
    if types is not None:
        message = "types is only supported for string selectors"
        raise ValueError(message)
    if isinstance(tree, TreeIndex):
        if children is not None:
            message = "children cannot change traversal of an existing tree index"
            raise ValueError(message)
    else:
        tree = index_tree(tree, children=children or ui_children)
    root = tree.index(within) if within is not None else None
    start = 0 if root is None else root + (not include_scope)
    end = len(tree.matches) if root is None else tree.ends[root]
    available = tree.matches[start:end]
    found = tuple(
        cast(Match[T], tree.matches[index])
        for index in tree.candidates(selector._kind, selector._key, start, end)
        if selector._predicate(tree, index, root, localization)
    )
    return Query(found, selector.description, available)
