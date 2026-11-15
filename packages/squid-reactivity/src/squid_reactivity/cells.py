"""Versioned cell storage and signals for immediate framework notifications.

Cell reads combine transaction visibility with dependency capture. Signals publish
changes directly for work such as operation progress and history bookkeeping.
"""

import uuid
from enum import Enum, auto
from typing import TYPE_CHECKING, Final, override

from squid_reactivity._runtime import _bump_epoch
from squid_reactivity.addresses import CellAddress, SubscriberRegistry, SubscriptionSource, publish
from squid_reactivity.tracking import _DEPENDENCY_COLLECTOR, _ReactiveSource
from squid_reactivity.transactions.state import _READ_CONSISTENCY, _active, _ReadConsistency

if TYPE_CHECKING:
    from squid_reactivity.topics import DependencyAddress


class _Missing(Enum):
    VALUE = auto()


_MISSING: Final = _Missing.VALUE
"""A cell that has never been assigned. Distinct from None, which is a real value."""


class _Cell[ValueT](_ReactiveSource):
    """Store one field's value, version, and optional publication address.

    Reactive fields, including component fields, have CellAddress values. Topics
    use cells with Topic addresses and no payload. Addressed reads can
    be checked for conflicts and collected as render subscriptions.
    """

    __slots__ = ("__weakref__", "_address", "identity", "value", "version")

    def __init__(
        self, value: ValueT | _Missing = _MISSING, version: int = 0, address: DependencyAddress | None = None
    ) -> None:
        self.identity = uuid.uuid7()
        self.value: ValueT | _Missing = value
        self.version = version
        self.address = address

    @property
    @override
    def address(self) -> DependencyAddress | None:
        return self._address

    @address.setter
    def address(self, value: DependencyAddress | None) -> None:
        self._address = value

    @property
    def label(self) -> str:
        """Return the readable address, or an opaque slot ID for an unaddressed cell."""
        return f"slot:{self.identity}" if self.address is None else str(self.address)

    def staged_value(self) -> ValueT | _Missing:
        """Return the active transaction's pending value, or _MISSING if none is staged."""
        current = _active()
        if current is None:
            return _MISSING
        entry = current.staged(self)
        return _MISSING if entry is None else entry.value

    @override
    def dependencies(self) -> tuple[_ReactiveSource, ...]:
        return ()

    @override
    def settle(self) -> int:
        """Return the version a reader should compare against. A cell is always settled."""
        current = _active()
        if current is not None:
            entry = current.staged(self)
            if entry is not None:
                return entry.version
        return self.version

    def track(self, version: int) -> None:
        """Record a read of this cell at `version` with whatever is consuming it."""
        consumer = _DEPENDENCY_COLLECTOR.get()
        if consumer is not None:
            consumer.sources[self] = version

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

    def write(self, value: ValueT | _Missing) -> None:
        self.value = value
        self.touch()

    def touch(self) -> None:
        """Advance the version without replacing the value."""
        self.version += 1
        _bump_epoch()


class Signal(_ReactiveSource, SubscriptionSource):
    """A reactive source with no value, for changes that publish outside transactions.

    Use one for state kept in ordinary attributes that readers should still follow, such
    as operation progress or undo-history bookkeeping. Call `track` wherever that state is
    read and `notify` after changing it. A computed value or render that tracked the
    signal then refreshes like any other reader.

    `notify` publishes immediately, even inside a transaction, and a rollback does not
    undo it. State that should commit and roll back with an action belongs in `state()`.
    """

    def __init__(self) -> None:
        self._reactive_subscribers: SubscriberRegistry = {}
        self.address = CellAddress(self, "revision")
        self.version = 0

    @property
    @override
    def address(self) -> CellAddress:
        return self._address

    @address.setter
    def address(self, value: CellAddress) -> None:
        self._address = value

    def track(self) -> None:
        """Make the computation or render now running depend on this signal."""
        consumer = _DEPENDENCY_COLLECTOR.get()
        if consumer is not None:
            consumer.sources[self] = self.version

    @override
    def dependencies(self) -> tuple[_ReactiveSource, ...]:
        return ()

    @override
    def settle(self) -> int:
        return self.version

    def notify(self) -> None:
        """Tell readers that tracked this signal that the state behind it changed."""
        self.version += 1
        _bump_epoch()
        publish(self.address)
