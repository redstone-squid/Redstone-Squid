"""Install Discord dynamic items and track routers already attached to each client."""

import re
import weakref
from typing import TYPE_CHECKING, Any, Self, override

import discord

from ._contracts import (
    RouteComponent,
)

if TYPE_CHECKING:
    from ._router import Router


_INSTALLED: weakref.WeakKeyDictionary[discord.Client, list[Router[Any]]] = weakref.WeakKeyDictionary()
"""Routers installed per client, so `register` can refuse the second router a click would wake."""


def routers[ClientT: discord.Client](client: ClientT) -> tuple[Router[ClientT], ...]:
    """Return a read-only snapshot of the routers installed on ``client``."""
    return tuple(_INSTALLED.get(client, ()))


def install(router: Router[Any], client: discord.Client) -> None:
    """Freeze route structure and install one dynamic dispatcher per router and client."""
    for group in router._groups:
        group._freeze()
    installed = _INSTALLED.setdefault(client, [])
    if router in installed:
        return
    for other in installed:
        collision = router._collision(other)
        if collision is not None:
            message = f"client already has a router this one collides with: {collision}"
            raise ValueError(message)
    router._registered = True
    installed.append(router)
    client.add_dynamic_items(_dispatch_item(router))


def _dispatch_item(router: Router[Any]) -> type[discord.ui.DynamicItem[discord.ui.Item[Any]]]:
    """Build the one `DynamicItem` subclass that carries ``router``'s whole route table."""

    class RoutedDispatch(  # pyrefly: ignore[invalid-inheritance]
        discord.ui.DynamicItem[discord.ui.Item[Any]],
        template=router.template(),
    ):
        @classmethod
        @override
        async def from_custom_id(  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
            cls,
            interaction: discord.Interaction[Any],
            item: discord.ui.Item[Any],
            match: re.Match[str],
            /,
        ) -> Self:
            return cls(item)

        @override
        async def callback(self, interaction: discord.Interaction[Any]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
            if isinstance(self.item, discord.ui.Select):
                await router.dispatch(
                    interaction,
                    self.custom_id,
                    component=RouteComponent.SELECT,
                    values=tuple(self.item.values),
                )
                return
            await router.dispatch(interaction, self.custom_id)

    return RoutedDispatch
