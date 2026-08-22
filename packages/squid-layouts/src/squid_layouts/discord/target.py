"""Discord Components V2 target profile and measured native extension."""

from collections.abc import Callable

import discord

from squid_layouts.discord.inspection import cost
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.planning.target import PreparedExtension, TargetProfile
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
        return PreparedExtension(
            # One definition of what a component costs, shared with `sl.discord.cost`.
            cost=cost(item),
            scene_payload={"native_kind": type(item).__name__},
            resource=item,
        )


class Target(TargetProfile):
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
                    "forms.discord.entity",
                    "forms.discord.file",
                    "forms.modal",
                    "layout.container",
                    "layout.gallery",
                    "layout.section",
                }
            ),
            limits=limits,
            extensions={"discord.item": _DiscordItemExtension()},
            # `resources` is left empty on purpose: V2Limits.budgets already names every
            # message-wide axis and its cap, and two declarations of the same thing drift.
        )


def NativeItem(factory: Callable[[], discord.ui.Item], *, fallback: Node) -> Extension:
    """Create a measured Discord item with a required portable fallback."""
    return Extension(kind="discord.item", version=1, payload=factory, fallback=fallback)


DEFAULT_TARGET = Target()
