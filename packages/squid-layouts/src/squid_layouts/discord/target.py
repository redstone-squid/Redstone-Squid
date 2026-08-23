"""Discord target profiles — one per message mode — and the measured native extension."""

from collections.abc import Callable

import discord

from squid_layouts.discord.inspection import cost
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.classic import CLASSIC_DIALECT
from squid_layouts.planning.limits import CLASSIC_LIMITS, LIMITS, ClassicLimits, V2Limits
from squid_layouts.planning.target import PreparedExtension, TargetProfile
from squid_layouts.planning.v2 import V2_DIALECT
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


V2_CAPABILITIES = frozenset(
    {
        "actions.buttons",
        "actions.discord.premium",
        "actions.select",
        "actions.discord.entity",
        "extension.discord.item",
        "forms.discord.entity",
        "forms.discord.file",
        "forms.discord.checkbox_group",
        "forms.modal",
        "layout.container",
        "layout.gallery",
        "layout.section",
    }
)

CLASSIC_CAPABILITIES = frozenset(
    {
        "actions.buttons",
        "actions.discord.premium",
        "actions.select",
        "actions.discord.entity",
        "forms.modal",
        "forms.discord.checkbox_group",
        "layout.embed",
        "layout.embed_fields",
        "message.content",
    }
)
"""What a classic message can do, and by omission what it cannot.

No `layout.container`, `layout.section`, or `layout.gallery`: those are Components V2
structures with no classic equivalent, and a ladder rung requiring one is dropped before the
solver ever sees it rather than reinterpreted into something the author did not write. No
`extension.discord.item` either — a native V2 item lowers to its required portable fallback.
"""


class Target(TargetProfile):
    """One Discord message mode's capabilities, limits, and shape.

    Built through :meth:`v2` or :meth:`classic` rather than directly. A bare `Target(limits)`
    could not say which mode it meant, and the mode is the one thing a target exists to fix:
    it decides the dialect, the renderer, the view type, and whether the message may carry
    content at all.
    """

    @classmethod
    def v2(cls, *, limits: V2Limits = LIMITS) -> Target:
        """A Components V2 message: a `LayoutView` owning the whole message."""
        return cls(
            id="discord.components-v2",
            version=1,
            capabilities=V2_CAPABILITIES,
            limits=limits,
            extensions={"discord.item": _DiscordItemExtension()},
            dialect=V2_DIALECT,
            # `resources` is left empty on purpose: the limits already name every
            # message-wide axis and its cap, and two declarations of the same thing drift.
        )

    @classmethod
    def classic(cls, *, limits: ClassicLimits = CLASSIC_LIMITS) -> Target:
        """A pre-Components-V2 message: content, embeds, and up to five action rows."""
        return cls(
            id="discord.components-v1",
            version=1,
            capabilities=CLASSIC_CAPABILITIES,
            limits=limits,
            dialect=CLASSIC_DIALECT,
        )


def NativeItem(factory: Callable[[], discord.ui.Item], *, fallback: Node) -> Extension:
    """Create a measured Discord item with a required portable fallback."""
    return Extension(kind="discord.item", version=1, payload=factory, fallback=fallback)


V2_TARGET = Target.v2()
"""The Components V2 target with Discord's current limits."""

CLASSIC_TARGET = Target.classic()
"""The classic-message target with Discord's current limits."""
