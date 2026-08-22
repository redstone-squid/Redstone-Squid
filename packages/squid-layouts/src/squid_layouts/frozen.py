"""Immutable value containers for things a component keeps in declared state."""

from collections.abc import Iterable, Iterator, Mapping
from typing import Any, final


@final
class FrozenMapping[KeyT, ValueT](Mapping[KeyT, ValueT]):
    """A mapping that is immutable all the way down to its own hash.

    ``MappingProxyType`` is read-only but neither hashable nor proof against whoever still
    holds the dict underneath, and state values must be both. Iteration keeps insertion
    order, because rendering reads it; equality and hashing ignore it, as a mapping's should.
    """

    __slots__ = ("_entries", "_hash")

    def __init__(self, entries: Mapping[KeyT, ValueT] | Iterable[tuple[KeyT, ValueT]] = ()) -> None:
        self._entries: dict[KeyT, ValueT] = dict(entries)
        self._hash: int | None = None

    def __getitem__(self, key: KeyT) -> ValueT:
        return self._entries[key]

    def __iter__(self) -> Iterator[KeyT]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __hash__(self) -> int:
        # Computed once and kept: a state value is hashed on every assignment, and a mapping
        # deep enough to be worth freezing is not cheap to walk.
        if self._hash is None:
            self._hash = hash(frozenset(self._entries.items()))
        return self._hash

    def __repr__(self) -> str:
        return f"FrozenMapping({self._entries!r})"

    def __reduce__(self) -> tuple[Any, ...]:
        return (FrozenMapping, (self._entries,))
