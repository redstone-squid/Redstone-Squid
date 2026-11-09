"""Identify framework event handlers whose fields can be rebound after a cache hit.

The planner can keep the handler's structure in a cached plan while replacing
its live callbacks with those from the current document.
"""

from abc import abstractmethod

from squid_ui.handlers import _HandlerComposition


class GeneratedHandler[EventT](_HandlerComposition):
    """A typed event adapter whose fields fully describe framework-generated behavior."""

    @abstractmethod
    async def __call__(self, event: EventT) -> None: ...
