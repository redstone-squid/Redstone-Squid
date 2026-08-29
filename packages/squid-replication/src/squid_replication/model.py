"""Backend-neutral immutable values exposed by replicated documents."""

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

type ReplicatedPrimitive = None | bool | int | float | str
type ReplicatedValue = ReplicatedPrimitive | tuple[ReplicatedValue, ...] | Mapping[str, ReplicatedValue]

_MAX_JSON_DEPTH = 16
_MIN_INT64 = -(2**63)
_MAX_INT64 = 2**63 - 1


def freeze_value(value: object, *, depth: int = 0) -> ReplicatedValue:
    """Validate and deeply freeze one JSON-like replicated value."""
    if depth > _MAX_JSON_DEPTH:
        message = f"replicated values cannot exceed {_MAX_JSON_DEPTH} nested levels"
        raise ValueError(message)
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        if not _MIN_INT64 <= value <= _MAX_INT64:
            message = "replicated value integers must fit a signed 64-bit value"
            raise ValueError(message)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            message = "replicated value floats must be finite"
            raise ValueError(message)
        return value
    if isinstance(value, list | tuple):
        return tuple(freeze_value(item, depth=depth + 1) for item in value)
    if isinstance(value, Mapping):
        frozen: dict[str, ReplicatedValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                message = "replicated object keys must be strings"
                raise TypeError(message)
            frozen[key] = freeze_value(item, depth=depth + 1)
        return MappingProxyType(frozen)
    message = f"unsupported replicated value {type(value).__name__}"
    raise TypeError(message)


def thaw_value(value: ReplicatedValue) -> object:
    """Convert one frozen public value to the mutable shape accepted by Loro."""
    if isinstance(value, tuple):
        return [thaw_value(item) for item in value]
    if isinstance(value, Mapping):
        return {key: thaw_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class ReplicatedItem:
    """One stable logical item in a movable replicated list."""

    item_id: uuid.UUID
    value: ReplicatedValue


@dataclass(frozen=True, slots=True)
class ReplicatedTreeNode:
    """One immutable logical node in a replicated tree snapshot."""

    node_id: uuid.UUID
    parent_id: uuid.UUID | None
    children: tuple[uuid.UUID, ...]
    metadata: Mapping[str, ReplicatedValue]


@dataclass(frozen=True, slots=True)
class ReplicatedTreeSnapshot:
    """An immutable forest indexed by stable logical node identity."""

    roots: tuple[uuid.UUID, ...]
    nodes: tuple[ReplicatedTreeNode, ...]

    def node(self, node_id: uuid.UUID) -> ReplicatedTreeNode | None:
        return next((node for node in self.nodes if node.node_id == node_id), None)


@dataclass(frozen=True, slots=True)
class ReplicatedSnapshot:
    """A deeply immutable snapshot of every named container in one document."""

    counters: tuple[tuple[str, int], ...] = ()
    sets: tuple[tuple[str, frozenset[str]], ...] = ()
    texts: tuple[tuple[str, str], ...] = ()
    lists: tuple[tuple[str, tuple[ReplicatedValue, ...]], ...] = ()
    movable_lists: tuple[tuple[str, tuple[ReplicatedItem, ...]], ...] = ()
    maps: tuple[tuple[str, tuple[tuple[str, ReplicatedValue], ...]], ...] = ()
    trees: tuple[tuple[str, ReplicatedTreeSnapshot], ...] = ()

    def counter(self, path: str) -> int:
        return dict(self.counters).get(path, 0)

    def tagged_set(self, path: str) -> frozenset[str]:
        return dict(self.sets).get(path, frozenset())

    def text(self, path: str) -> str:
        return dict(self.texts).get(path, "")

    def sequence(self, path: str) -> tuple[ReplicatedValue, ...]:
        return dict(self.lists).get(path, ())

    def movable(self, path: str) -> tuple[ReplicatedItem, ...]:
        return dict(self.movable_lists).get(path, ())

    def mapping(self, path: str) -> Mapping[str, ReplicatedValue]:
        return MappingProxyType(dict(dict(self.maps).get(path, ())))

    def tree(self, path: str) -> ReplicatedTreeSnapshot:
        return dict(self.trees).get(path, ReplicatedTreeSnapshot((), ()))


__all__ = [
    "ReplicatedItem",
    "ReplicatedPrimitive",
    "ReplicatedSnapshot",
    "ReplicatedTreeNode",
    "ReplicatedTreeSnapshot",
    "ReplicatedValue",
]
