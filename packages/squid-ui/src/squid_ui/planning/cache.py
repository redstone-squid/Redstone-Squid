"""Bounded callback-free partition hints, recertified against each new input."""

from collections import OrderedDict
from collections.abc import Mapping
from types import MappingProxyType

from squid_ui.planning.composition import OccurrenceId


class PlanCache:
    """Retain bounded, callback-free partition hints for shared composition.

    A hint suggests page boundaries; the planner certifies them against current
    content and target limits. Writes enforce capacity, evicting least-used hints.
    """

    def __init__(self, capacity: int = 32) -> None:
        if capacity < 1:
            message = "plan cache capacity must be positive"
            raise ValueError(message)
        self.capacity = capacity
        self._partitions: OrderedDict[str, Mapping[OccurrenceId, tuple[int, ...]]] = OrderedDict()

    def partition_hint(self, key: str) -> Mapping[OccurrenceId, tuple[int, ...]] | None:
        """Return prior cuts as a search hint, never as a certificate of feasibility."""
        try:
            cuts = self._partitions[key]
        except KeyError:
            return None
        self._partitions.move_to_end(key)
        return cuts

    def remember_partitions(self, key: str, cuts: Mapping[OccurrenceId, tuple[int, ...]]) -> None:
        self._partitions[key] = MappingProxyType(dict(cuts))
        self._partitions.move_to_end(key)
        while len(self._partitions) > self.capacity:
            self._partitions.popitem(last=False)

    def __len__(self) -> int:
        return len(self._partitions)
