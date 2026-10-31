"""Reusable host defaults for Discord message roots."""

from dataclasses import dataclass
from typing import Unpack

from squid_ui.runtime.component import Component
from squid_ui_discord.access import AccessPolicy
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import (
    MessageRootConfig,
)
from squid_ui_discord.message_root_contracts import (
    MessageRootOptions as MessageRootOptions,
)


@dataclass(frozen=True, slots=True)
class MessageRootDefaults(MessageRootConfig):
    """Host-wide values used to construct message roots.

    The values and their defaults come from :class:`MessageRootConfig`; this adds the one
    thing a host wants from them, which is to build a mount. Access remains deliberately
    absent: it identifies the actor allowed to use a specific mount and must be supplied at
    each construction site.
    """

    def mount[RenderTargetT](
        self,
        component: Component[RenderTargetT],
        *,
        access: AccessPolicy,
        **overrides: Unpack[MessageRootOptions],
    ) -> MessageRoot[RenderTargetT]:
        """Construct a mount, applying per-call overrides over these defaults."""
        return MessageRoot(component, access=access, config=self, **overrides)


__all__ = ["MessageRootDefaults", "MessageRootOptions"]
