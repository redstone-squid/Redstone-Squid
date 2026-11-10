"""Retain one reactive object per key for an explicitly owned lifetime."""

from collections.abc import Callable, Hashable, Mapping
from types import MappingProxyType
from typing import override

from squid_reactivity.core import Reactive

type ReactiveFactory[KeyT, ValueT: Reactive] = Callable[[KeyT], ValueT]


class ReactivePool[KeyT: Hashable, ValueT: Reactive]:
    """Construct objects on demand and retain them until deletion or clearing.

    The factory receives the requested key and may capture application services.
    Keys belong to the pool; objects need no scope metadata or base initializer.
    Removing an object does not reset it or stop consumers still holding it.
    """

    def __init__(self, factory: ReactiveFactory[KeyT, ValueT]) -> None:
        self._factory = factory
        self._handles: dict[KeyT, ValueT] = {}
        self._constructing: set[KeyT] = set()

    def get(self, key: KeyT) -> ValueT:
        """Return the retained object, constructing it once on a cache miss."""
        if key in self._handles:
            return self._handles[key]
        return self._adopt(key, self._create(key))

    def get_existing(self, key: KeyT) -> ValueT | None:
        return self._handles.get(key)

    def delete(self, key: KeyT) -> ValueT | None:
        return self._handles.pop(key, None)

    def clear(self) -> None:
        self._handles.clear()

    def active(self) -> Mapping[KeyT, ValueT]:
        """Return a snapshot that retains the currently cached objects."""
        return MappingProxyType(dict(self._handles))

    def _create(self, key: KeyT) -> ValueT:
        if key in self._constructing:
            message = f"reactive object for key {key!r} is already being constructed"
            raise RuntimeError(message)
        self._constructing.add(key)
        try:
            created = self._factory(key)
        finally:
            self._constructing.remove(key)
        self._validate(key, created)
        return created

    def _validate(self, key: KeyT, created: ValueT) -> None:
        if not isinstance(created, Reactive):
            message = "reactive factory must return a Reactive object"
            raise TypeError(message)
        if any(other != key and held is created for other, held in self._handles.items()):
            message = "reactive factory reused one object for different keys"
            raise ValueError(message)

    def _adopt(self, key: KeyT, created: ValueT) -> ValueT:
        # A persistence owner may have awaited hydration since _create returned.
        if key in self._handles:
            return self._handles[key]
        self._validate(key, created)
        self._handles[key] = created
        return created

    @override
    def __repr__(self) -> str:
        return f"ReactivePool({len(self._handles)} active)"


__all__ = ["ReactiveFactory", "ReactivePool"]
