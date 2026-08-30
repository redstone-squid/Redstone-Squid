"""Installation configuration for the owner-scoped Discord facade."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from squid_ui.profiling import Profiler
from squid_ui.runtime.topics import TopicBus
from squid_ui_discord.contracts import LocalizationResolver
from squid_ui_discord.message_root_options import MessageRootDefaults

if TYPE_CHECKING:
    from squid_ui_discord.response import ResponseSpec


def _response_spec() -> ResponseSpec:
    from squid_ui_discord.response import ResponseSpec

    return ResponseSpec()


@dataclass(frozen=True, slots=True)
class DiscordUIConfig:
    """Process-wide rendering services and response defaults."""

    defaults: MessageRootDefaults = field(default_factory=MessageRootDefaults)
    responses: ResponseSpec = field(default_factory=_response_spec)
    localization: LocalizationResolver | None = None
    bus: TopicBus | None = None
    profiler: Profiler | None = None


__all__ = ["DiscordUIConfig"]
