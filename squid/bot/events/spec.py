"""Subscription interface exported by the Discord domain-event consumer."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from squid.bot.extensions import ExtensionSpec
from squid.core.events.dispatcher import EventHandler


class DomainEventSubscriptions(Protocol):
    def subscribe(self, handlers: Mapping[str, EventHandler]) -> Callable[[], Awaitable[None]]:
        """Register handlers and return a callback to drain and detach them."""
        ...


extension: ExtensionSpec[DomainEventSubscriptions] = ExtensionSpec(id="domain-events", module="squid.bot.events.cog")
