"""Marker for framework-generated event adapters."""

from abc import ABC, abstractmethod


class GeneratedHandler[EventT](ABC):
    """A dataclass handler the plan cache stores by type and init fields and rebuilds on replay.

    A callback the framework makes itself is not one of the document's values, so it has no slot
    in a cached template and forces every hit to re-lower; a `GeneratedHandler` is rebuilt from
    its fields instead. A subclass must be a dataclass whose init fields fully determine its behavior.
    """

    @abstractmethod
    async def __call__(self, event: EventT) -> None:
        """Handle one event; the framework calls this in place of an author callback."""
        ...
