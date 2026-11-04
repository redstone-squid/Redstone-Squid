"""Reusable host defaults for Discord message roots."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Unpack

from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target, DiscordPy27Adapter, DiscordPyAdapter, DiscordTarget
from squid_ui_discord.access import AccessPolicy
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import (
    MessageRootBehaviorOptions,
    MessageRootConfig,
)
from squid_ui_discord.message_root_contracts import (
    MessageRootOptions as MessageRootOptions,
)

if TYPE_CHECKING:
    from squid_ui_discord.request import Request


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
        component: Component[RenderTargetT, Request],
        *,
        access: AccessPolicy,
        **overrides: Unpack[MessageRootBehaviorOptions],
    ) -> MessageRoot[RenderTargetT, AdapterT]:
        """Construct a message root, applying per-call overrides over these defaults."""
        return MessageRoot(component, access=access, config=self, **overrides)


__all__ = ["MessageRootDefaults", "MessageRootOptions"]
