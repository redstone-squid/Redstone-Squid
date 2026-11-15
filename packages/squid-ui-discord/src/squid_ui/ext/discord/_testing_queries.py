"""Read-only selector adapters for native Discord UI trees."""

from collections.abc import Iterator, Mapping
from typing import Any, overload

import discord
from squid_ui.content.text import NEUTRAL, Localization
from squid_ui.selectors import Match, Query, Selector, Snapshot, parse
from squid_ui.selectors import query as query_tree
from squid_ui.selectors import snapshot as capture_snapshot

_DISCORD_TYPES: dict[str, type] = {
    name: value
    for name, value in vars(discord.ui).items()
    if isinstance(value, type)
    and issubclass(value, discord.ui.Item | discord.ui.View | discord.ui.LayoutView | discord.ui.Modal)
}
_DISCORD_TYPES.update(SelectOption=discord.SelectOption, MediaGalleryItem=discord.MediaGalleryItem)


def _children(node: object) -> Iterator[tuple[str, object]]:
    if isinstance(
        node, discord.ui.View | discord.ui.LayoutView | discord.ui.Modal | discord.ui.ActionRow | discord.ui.Container
    ):
        yield from ((f"children.{index}", child) for index, child in enumerate(node.children))
    elif isinstance(node, discord.ui.Section):
        yield from ((f"children.{index}", child) for index, child in enumerate(node.children))
        yield "accessory", node.accessory
    elif isinstance(node, discord.ui.Label):
        yield "component", node.component
    elif isinstance(node, discord.ui.Select):
        yield from ((f"options.{index}", option) for index, option in enumerate(node.options))
    elif isinstance(node, discord.ui.MediaGallery):
        yield from ((f"items.{index}", item) for index, item in enumerate(node.items))


def snapshot(view: object) -> Snapshot:
    """Capture a View, LayoutView, modal, native item, or sequence without rendering.

    Native child relationships, section accessories, select options, and gallery items
    are traversed. Callback and parent references are not. Reuse the returned snapshot
    when comparing order or scoping queries with matches from an earlier query.
    """
    if isinstance(view, Snapshot):
        return view
    return capture_snapshot(view, children=_children)


@overload
def query[T](
    view: object,
    selector: Selector[T],
    *,
    within: Match[Any] | None = None,
    include_scope: bool = False,
    localization: Localization = NEUTRAL,
) -> Query[T]: ...


@overload
def query(
    view: object,
    selector: str,
    *,
    types: Mapping[str, type] | None = None,
    within: Match[Any] | None = None,
    include_scope: bool = False,
    localization: Localization = NEUTRAL,
) -> Query[object]: ...


def query[T](
    view: object,
    selector: Selector[T] | str,
    *,
    types: Mapping[str, type] | None = None,
    within: Match[Any] | None = None,
    include_scope: bool = False,
    localization: Localization = NEUTRAL,
) -> Query[T] | Query[object]:
    """Query existing native Discord UI, using Button, TextDisplay, etc. as string type names.

    Attributes retain their Discord names, such as custom_id and disabled. Semantic
    control keys are not reconstructed from custom IDs. Pass an explicit snapshot
    when using within to scope a query to an existing match.
    """
    if isinstance(selector, str):
        return query_tree(
            snapshot(view),
            parse(selector, types=_DISCORD_TYPES | dict(types or {})),
            within=within,
            include_scope=include_scope,
            localization=localization,
        )
    if types is not None:
        message = "types is only supported for string selectors"
        raise ValueError(message)
    return query_tree(snapshot(view), selector, within=within, include_scope=include_scope, localization=localization)
