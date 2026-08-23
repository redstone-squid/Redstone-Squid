"""Pins the `state()` and `cell()` overloads under `just typecheck`; nothing here runs.

A `dict`, `list` or `set` default or factory must declare the read-only ABC, which is what
makes a concrete annotation and every mutating method a type error at the use sites. A
namespace's scope is typed by its parameter, and unparameterised means `Shared[None]`.
"""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import Any, assert_type

from squid_layouts import Shared, addresses, cell, state
from squid_layouts.topics import TopicBus

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

assert_type(cell({"a": 1}), Mapping[str, int])
assert_type(cell(["a"]), Sequence[str])
assert_type(cell({"a"}), AbstractSet[str])
assert_type(cell(factory=lambda: {"a": 1}), Mapping[str, int])
assert_type(cell(factory=lambda: ["a"]), Sequence[str])
assert_type(cell(factory=lambda: {"a"}), AbstractSet[str])
assert_type(cell(("a",)), tuple[str])
assert_type(cell(0), int)
assert_type(cell(factory=int), int)


class Anonymous(Shared):
    flag: bool = cell(default=False)


class Scoped(Shared[int]):
    theme: str = cell("system")


assert_type(Anonymous(bus).scope, None)
assert_type(Scoped(bus, 7).scope, int)
assert_type(Scoped(bus, 7).theme, str)
assert_type(addresses(lambda: Scoped(bus, 7).theme), tuple[Any, ...])
