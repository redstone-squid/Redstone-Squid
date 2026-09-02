"""Installation configuration for the Discord facade."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from squid_ui.document import DocumentLike
from squid_ui.profiling import Profiler
from squid_ui.runtime.topics import TopicBus
from squid_ui_discord.contracts import LocalizationResolver
from squid_ui_discord.delivery import DeliveryResult
from squid_ui_discord.message_root_options import MessageRootDefaults

if TYPE_CHECKING:
    from squid_ui_discord.request import Request
    from squid_ui_discord.response import ResponseSpec

type ErrorRenderer = Callable[[Request[Any], Exception], DocumentLike]
type ErrorObserver = Callable[[Request[Any], Exception, DeliveryResult | None], Awaitable[None]]


def _response_spec() -> ResponseSpec:
    from squid_ui_discord.response import ResponseSpec

    return ResponseSpec()


@dataclass(frozen=True, slots=True)
class ErrorPolicy:
    """What a `pending=` command shows and records when its handler raises.

    `render` replaces the pending card with a failure document; `observe` runs afterwards
    with the delivery that card reached, if any. The error is re-raised either way.
    """

    render: ErrorRenderer | None = None
    observe: ErrorObserver | None = None


@dataclass(frozen=True, slots=True)
class DiscordUIConfig:
    """Process-wide rendering services and response defaults."""

    defaults: MessageRootDefaults = field(default_factory=MessageRootDefaults)
    responses: ResponseSpec = field(default_factory=_response_spec)
    localization: LocalizationResolver | None = None
    bus: TopicBus | None = None
    profiler: Profiler | None = None
    errors: ErrorPolicy = field(default_factory=ErrorPolicy)


__all__ = ["DiscordUIConfig", "ErrorObserver", "ErrorPolicy", "ErrorRenderer"]
