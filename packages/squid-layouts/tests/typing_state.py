"""Pins the `state()` and `state()` overloads under `just typecheck`; nothing here runs.

A `dict`, `list` or `set` default or factory must declare the read-only ABC, which is what
makes a concrete annotation and every mutating method a type error at the use sites. A
namespace's scope is typed by its parameter, and unparameterised means `Shared[None]`.
"""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import Any, assert_type

from squid_layouts import (
    AtomicResource,
    AtomicResourceState,
    Component,
    Failed,
    Pending,
    Ready,
    Resource,
    ResourceDelivery,
    ResourceState,
    Shared,
    addresses,
    resource,
    state,
)
from squid_layouts.runtime.topics import TopicBus

bus = TopicBus()

assert_type(state({"a": 1}), Mapping[str, int])
assert_type(state(["a"]), Sequence[str])
assert_type(state({"a"}), AbstractSet[str])
assert_type(state(factory=lambda: {"a": 1}), Mapping[str, int])
assert_type(state(factory=lambda: ["a"]), Sequence[str])
assert_type(state(factory=lambda: {"a"}), AbstractSet[str])
assert_type(state(("a",)), tuple[str])
assert_type(state(frozenset({"a"})), frozenset[str])
assert_type(state(0), int)
assert_type(state(factory=int), int)

assert_type(state({"a": 1}), Mapping[str, int])
assert_type(state(["a"]), Sequence[str])
assert_type(state({"a"}), AbstractSet[str])
assert_type(state(factory=lambda: {"a": 1}), Mapping[str, int])
assert_type(state(factory=lambda: ["a"]), Sequence[str])
assert_type(state(factory=lambda: {"a"}), AbstractSet[str])
assert_type(state(("a",)), tuple[str])
assert_type(state(0), int)
assert_type(state(factory=int), int)


class Anonymous(Shared):
    flag: bool = state(default=False)


class Scoped(Shared[int]):
    theme: str = state("system")


assert_type(Anonymous(bus).scope, None)
assert_type(Scoped(bus, 7).scope, int)
assert_type(Scoped(bus, 7).theme, str)
assert_type(addresses(lambda: Scoped(bus, 7).theme), tuple[Any, ...])


class ResourceTypes(Component):
    @resource(delivery=ResourceDelivery.ATOMIC)
    async def atomic(self) -> int:
        return 1

    @resource
    async def visible(self) -> int:
        return 1


assert_type(ResourceTypes().atomic, AtomicResource[int])
assert_type(ResourceTypes().atomic.state, AtomicResourceState[int])
assert_type(ResourceTypes().visible, Resource[int])
assert_type(ResourceTypes().visible.state, ResourceState[int])
assert_type(Ready[int](1), Ready[int])
assert_type(Failed[int](ValueError()), Failed[int])
assert_type(Pending[int](), Pending[int])
