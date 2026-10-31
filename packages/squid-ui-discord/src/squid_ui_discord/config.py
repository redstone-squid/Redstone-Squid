"""Installation configuration for the Discord facade."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from squid_reactivity.topics import TopicBus
from squid_ui.profiling import Profiler
from squid_ui_discord.contracts import DocumentContent, LocalizationResolver
from squid_ui_discord.delivery import DeliveryResult
from squid_ui_discord.message_root_options import MessageRootDefaults
from squid_ui_discord.response import ResponseSpec

if TYPE_CHECKING:
    from squid_ui_discord.request import Request

type ErrorRenderer = Callable[[Request[Any], Exception], DocumentContent]
type ErrorObserver = Callable[[Request[Any], Exception, DeliveryResult | None], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ErrorPolicy:
    """How commands and component callbacks present and record failures.

    `render` replaces the pending card with a failure document; `observe` runs afterwards
    with the delivery that notice reached, if any. Pending commands re-raise the error;
    component dispatch reports it after rolling back the failed action.
    """

    render: ErrorRenderer | None = None
    observe: ErrorObserver | None = None


@dataclass(frozen=True, slots=True)
class Config:
    """Process-wide rendering services and response defaults."""

    # `default_factory=MessageRootDefaults` leaves both parameters unsolved instead of falling
    # back to their PEP 696 defaults; calling the class applies them.
    defaults: MessageRootDefaults = field(default_factory=lambda: MessageRootDefaults())
    responses: ResponseSpec = field(default_factory=ResponseSpec)
    localization: LocalizationResolver | None = None
    bus: TopicBus | None = None
    profiler: Profiler | None = None
    errors: ErrorPolicy = field(default_factory=ErrorPolicy)

    def __post_init__(self) -> None:
        if self.defaults.on_error is not None:
            message = "configure application errors with Config.errors, not MessageRootDefaults.on_error"
            raise TypeError(message)


__all__ = ["Config", "ErrorObserver", "ErrorPolicy", "ErrorRenderer"]
