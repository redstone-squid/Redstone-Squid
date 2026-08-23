"""Pins the `state()` overloads under `just typecheck`; nothing here runs.

A `dict`, `list` or `set` default or factory must declare the read-only ABC, which is what
makes a concrete annotation and every mutating method a type error at the use sites.
"""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import assert_type

from squid_layouts import state

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
