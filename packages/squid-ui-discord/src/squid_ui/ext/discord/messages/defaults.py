"""Reusable host defaults for Discord message roots."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Unpack

from squid_ui.ext.discord.access import AccessPolicy
from squid_ui.ext.discord.messages.contracts import (
    MessageRootBehaviorOptions,
    MessageRootConfig,
)
from squid_ui.ext.discord.messages.contracts import (
    MessageRootOptions as MessageRootOptions,
)
from squid_ui.ext.discord.messages.root import MessageRoot
from squid_ui.ext.discord.target_types import DiscordPy27Adapter, DiscordPyAdapter
from squid_ui.runtime.definition import Component
from squid_ui.target_types import ComponentsV2Target, DiscordTarget

if TYPE_CHECKING:
    from squid_ui.ext.discord.request import Request


@dataclass(frozen=True, slots=True)
class MessageRootDefaults[
    RenderTargetT: DiscordTarget = ComponentsV2Target,
    AdapterT: DiscordPyAdapter = DiscordPy27Adapter,
](MessageRootConfig[RenderTargetT, AdapterT]):
    """Host-wide values used to construct message roots.

    Call ``mount(component, access=...)`` to construct a message root with these defaults
    and any per-call overrides. Access must be supplied for each root because the allowed
    actors depend on the particular presentation.
    """

    def mount(
        self,
        component: Component[RenderTargetT, Request[Any, Any]],
        *,
        access: AccessPolicy,
        **overrides: Unpack[MessageRootBehaviorOptions],
    ) -> MessageRoot[RenderTargetT, AdapterT]:
        """Construct a message root, applying per-call overrides over these defaults."""
        return MessageRoot(component, access=access, config=self, **overrides)


__all__ = ["MessageRootDefaults", "MessageRootOptions"]
