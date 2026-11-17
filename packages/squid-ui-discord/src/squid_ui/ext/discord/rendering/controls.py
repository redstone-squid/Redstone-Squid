"""Construct Discord controls shared by classic and Components V2 renderers.

Factories return fresh, unattached items. Each renderer owns row placement and
view assembly; routed items deliberately bypass the view's local dispatch table.
"""

from collections.abc import Callable
from typing import Any, override

import discord
from squid_ui.errors import DrawInvariantError
from squid_ui.ext.discord import scene
from squid_ui.ext.discord.emoji import discord_emoji
from squid_ui.interactions import ActionBinding
from squid_ui.planning.result import PlanResult

type Control = scene.Button | scene.Select | scene.EntitySelect
type Wire = Callable[[Control, ActionBinding], discord.ui.Item[Any]]


class RoutedItem(discord.ui.Button[Any]):
    """A button whose only dispatch path is a `Router`.

    Discord's payload is an ordinary button; the override is purely about discord.py's
    in-process bookkeeping. `ViewStore.add_view` files an item into the stored dispatch
    table only when `is_dispatchable()` is true, and `dispatch_view` runs *both* the dynamic
    lookup and that table. A stored routed button would therefore take a second dispatch
    whose callback is `Item`'s no-op — harmless for responses, but `_scheduled_task` resets
    the view's timeout expiry before awaiting it, so clicking a routed control inside a
    mounted message silently extended that mount's life.

    Staying out of the table gives the control exactly one dispatch path. Dynamic dispatch
    is unaffected: `schedule_dynamic_item_call` rebuilds the view with
    `LayoutView.from_message` and finds the base item by component type and custom id there,
    on stock `Button` objects this class never touches.
    """

    @override
    def is_dispatchable(self) -> bool:
        return False


class RoutedSelectItem(discord.ui.Select[Any]):
    """A stateless select kept out of a surrounding mount's stored dispatch table."""

    @override
    def is_dispatchable(self) -> bool:
        return False


def draw_link(node: scene.Link) -> discord.ui.Item[Any]:
    return discord.ui.Button(
        style=discord.ButtonStyle.link,
        label=node.label,
        url=node.url,
        emoji=discord_emoji(node.emoji),
        disabled=node.disabled,
    )


def draw_routed_button(node: scene.RoutedButton) -> discord.ui.Item[Any]:
    return RoutedItem(
        style=getattr(discord.ButtonStyle, node.style.value),
        label=node.label,
        custom_id=node.route_id,
        emoji=discord_emoji(node.emoji),
        disabled=node.disabled,
    )


def draw_routed_select(node: scene.RoutedSelect) -> discord.ui.Item[Any]:
    return RoutedSelectItem(
        options=[_option(option) for option in node.options],
        custom_id=node.route_id,
        placeholder=node.placeholder,
        min_values=node.min_values,
        max_values=node.max_values,
        disabled=node.disabled,
    )


def draw_control[BodyT: scene.Body](
    node: Control, *, plan: PlanResult[BodyT] | None, wire: Wire | None
) -> discord.ui.Item[Any]:
    if plan is None or wire is None:
        message = "interactive scene controls require a mounted Discord frontend"
        raise TypeError(message)
    binding = plan.bindings.get(node.action)
    if binding is None:
        message = f"scene action {node.action!r} has no binding"
        raise DrawInvariantError(message)
    return wire(node, binding)


def _option(option: scene.Option) -> discord.SelectOption:
    return discord.SelectOption(
        label=option.label,
        value=option.value,
        description=option.description,
        default=option.default,
        emoji=discord_emoji(option.emoji),
    )
