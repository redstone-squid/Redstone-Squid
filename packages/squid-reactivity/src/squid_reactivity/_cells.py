"""Store versioned field values and expose explicit invalidation signals.

Cell reads combine the active transaction's staged values with dependency capture.
Signals represent changes that have no stored value and notify subscribers directly.
"""

import uuid
from enum import Enum, auto
from typing import TYPE_CHECKING, Final, override

from squid_reactivity._addresses import CellAddress, SubscriberRegistry, SubscriptionSource, publish
from squid_reactivity._runtime import _bump_epoch
from squid_reactivity._tracking import _DEPENDENCY_COLLECTOR, _ReactiveSource
from squid_reactivity.transactions._state import _READ_CONSISTENCY, _active, _ReadConsistency

if TYPE_CHECKING:
    from squid_reactivity.topics import DependencyAddress


class _Missing(Enum):
    VALUE = auto()


_MISSING: Final = _Missing.VALUE
"""A cell that has never been assigned. Distinct from None, which is a real value."""


class _Cell[ValueT](_ReactiveSource):
    """Store one field's value, version, identity, and optional notification address.

    Reads return the active transaction's pending value when present and otherwise
    use committed storage. An unassigned cell contains _MISSING, distinct from None.
    Addressed reads can become transaction preconditions and reactive dependencies.

    Topic handles also use cells, solely to track a version without a value.
    """

    __slots__ = ("__weakref__", "_address", "identity", "value", "version")

    def __init__(
        self, value: ValueT | _Missing = _MISSING, version: int = 0, address: DependencyAddress | None = None
    ) -> None:
        self.identity = uuid.uuid7()
        self.value: ValueT | _Missing = value
        self.version = version
        self._address = address

    @property
    @override
    def address(self) -> DependencyAddress | None:
        return self._address

    @property
    def label(self) -> str:
        """Return the readable address, or an opaque slot ID for an unaddressed cell."""
        return f"slot:{self.identity}" if self.address is None else str(self.address)

    def read(self) -> ValueT | _Missing:
        """Return the value this reader should see, staged write included, and record it."""
        current = _active()
        if current is not None:
            entry = current.staged(self)
            if entry is not None:
                # A staged read does not observe committed state, so it adds no
                # commit-time version precondition.
                self.track(entry.version)
                return entry.value
            policy = _READ_CONSISTENCY.get()
            if self.address is not None and policy is not _ReadConsistency.RELAXED:
                current.observe(self, strong=policy is _ReadConsistency.STRONG)
        self.track(self.version)
        return self.value

    def staged_value(self) -> ValueT | _Missing:
        """Return the active transaction's pending value, or _MISSING if none is staged."""
        current = _active()
        if current is None:
            return _MISSING
        entry = current.staged(self)
        return _MISSING if entry is None else entry.value

    def write(self, value: ValueT | _Missing) -> None:
        self.value = value
        self.touch()

    def touch(self) -> None:
        """Advance the version without replacing the value."""
        self.version += 1
        _bump_epoch()

    def track(self, version: int) -> None:
        """Record a read of this cell at `version` with whatever is consuming it."""
        consumer = _DEPENDENCY_COLLECTOR.get()
        if consumer is not None:
            consumer.sources[self] = version

    @override
    def dependencies(self) -> tuple[_ReactiveSource, ...]:
        return ()

    @override
    def validated_version(self) -> int:
        """Return the staged version when present, otherwise the committed version."""
        current = _active()
        if current is not None:
            entry = current.staged(self)
            if entry is not None:
                return entry.version
        return self.version


class Signal(_ReactiveSource, SubscriptionSource):
    """A versioned notification source for state stored outside reactive fields.

    Call track() when a consumer reads the external state and notify() after it changes.
    For example, operation progress and undo bookkeeping use signals to invalidate
    dependent computations without moving their storage into state() fields.

    Notifications are immediate, even inside a transaction. Rollback cannot undo them;
    use state() for values whose writes should be staged and discarded together.
    """

    def __init__(self) -> None:
        self._reactive_subscribers: SubscriberRegistry = {}
        self._address = CellAddress(self, "revision")
        self.version = 0

    @property
    @override
    def address(self) -> CellAddress:
        return self._address

    def track(self) -> None:
        """Record this signal as a dependency of the active computation."""
        consumer = _DEPENDENCY_COLLECTOR.get()
        if consumer is not None:
            consumer.sources[self] = self.version

    def notify(self) -> None:
        """Tell readers that tracked this signal that the state behind it changed."""
        self.version += 1
        _bump_epoch()
        publish(self.address)

    @override
    def dependencies(self) -> tuple[_ReactiveSource, ...]:
        return ()

    @override
    def validated_version(self) -> int:
        return self.version
