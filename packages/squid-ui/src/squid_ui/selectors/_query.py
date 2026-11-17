"""Evaluate typed and string selectors against an existing tree index."""

from collections.abc import Mapping
from typing import Any, cast, overload

from squid_ui.content.text import NEUTRAL, Localization
from squid_ui.selectors.core import Query, QuerySnapshot, Selector
from squid_ui.selectors.parser import parse
from squid_ui.selectors.source import QueryRoot
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
    """Query a tree or follow a live QueryRoot using the same selector language.

    Raw trees retain their structural index. Live roots resolve the latest
    committed tree on each read. Use capture() for consistent multi-step reads;
    matches and within scopes always belong to one captured index.
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
    if isinstance(tree, QueryRoot):
        return Query(
            lambda: query(
                tree.read(),
                selector,
                children=children,
                within=within,
                include_scope=include_scope,
                localization=localization,
            ).capture()
        )
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
    captured = QuerySnapshot(found, selector.description, available)
    return Query(lambda: captured)
