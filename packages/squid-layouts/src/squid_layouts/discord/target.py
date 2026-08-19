"""Discord Components V2 target profile and measured native extension."""

from collections.abc import Callable

import discord

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.planning.target import PreparedExtension, ResourceCost, TargetProfile
from squid_layouts.primitives.nodes import Extension, Node


class _DiscordItemExtension:
    def prepare(self, payload: object) -> PreparedExtension:
        if not callable(payload):
            message = "discord.item extension payload must be a zero-argument factory"
            raise LayoutInvariantError(message)
        try:
            item = payload()
        except Exception as error:
            message = "discord.item factory failed during target planning"
            raise LayoutInvariantError(message) from error
        if not isinstance(item, discord.ui.Item):
            message = "discord.item factory did not return a discord.ui.Item"
            raise LayoutInvariantError(message)
        descendants = list(item.walk_children()) if hasattr(item, "walk_children") else []
        all_items = (item, *descendants)
        text = sum(len(child.content) for child in all_items if isinstance(child, discord.ui.TextDisplay))
        return PreparedExtension(
            cost=ResourceCost({"components": len(all_items), "display_text": text}),
            scene_payload={"native_kind": type(item).__name__},
            resource=item,
        )


class DiscordV2Target(TargetProfile):
    """Discord Components V2 capabilities and resource limits."""

    def __init__(self, limits: V2Limits = LIMITS) -> None:
        super().__init__(
            id="discord.components-v2",
            version=1,
            capabilities=frozenset(
                {
                    "actions.buttons",
                    "actions.select",
                    "extension.discord.item",
                    "forms.modal",
                    "layout.container",
                    "layout.gallery",
                    "layout.section",
                }
            ),
            limits=limits,
            extensions={"discord.item": _DiscordItemExtension()},
        )


def NativeItem(factory: Callable[[], discord.ui.Item], *, fallback: Node) -> Extension:
    """Create a measured Discord item with a required portable fallback."""
    return Extension(kind="discord.item", version=1, payload=factory, fallback=fallback)


DISCORD_V2 = DiscordV2Target()
