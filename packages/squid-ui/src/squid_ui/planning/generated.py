"""Marker for framework-generated event adapters."""

from abc import ABC, abstractmethod


class GeneratedHandler[EventT](ABC):
    """A typed event adapter whose fields fully describe framework-generated behavior."""

    @abstractmethod
    async def __call__(self, event: EventT) -> None: ...
