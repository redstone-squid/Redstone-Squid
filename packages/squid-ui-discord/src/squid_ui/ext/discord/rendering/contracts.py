"""The drawing contract shared by live classic and Components V2 messages."""

from typing import Protocol, override

from squid_ui.ext.discord import scene
from squid_ui.ext.discord.messages.payload import MessagePayload
from squid_ui.ext.discord.rendering.controls import Wire
from squid_ui.planning.result import PlanResult
from squid_ui.renderer import Renderer


class MountedRenderer[BodyT: scene.Body](Renderer[BodyT, MessagePayload], Protocol):
    """A message renderer that accepts a mount's control-wiring callback.

    wire builds SDK items bound to the current mount generation using the matching
    plan's bindings. Both classic and Components V2 renderers implement this contract.
    """

    @override
    def draw(
        self,
        document: scene.Scene[BodyT],
        *,
        plan: PlanResult[BodyT] | None = None,
        wire: Wire | None = None,
    ) -> MessagePayload: ...
