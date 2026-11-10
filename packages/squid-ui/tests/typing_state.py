"""Static checks for state overloads, shared scopes, resource results, and context values.

Mutable container inputs expose read-only ABC types to callers. Models declare
their own constructor parameters and ordinary metadata. This file
is checked by the project type checker and is not a pytest runtime test.
"""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import Any, assert_type, override

from squid_reactivity.topics import LocalTopicBus
from squid_ui import Component, ContextKey, Reactive, paragraph, resource, state
from squid_ui.runtime import (
    AtomicResource,
    AtomicResourceStatus,
    Failed,
    Pending,
    PendingMode,
    Ready,
    Resource,
    ResourceStatus,
    addresses,
)
from squid_ui.semantic import Paragraph

bus = LocalTopicBus()

assert_type(state({"a": 1}), Mapping[str, int])
assert_type(state(["a"]), Sequence[str])
assert_type(state({"a"}), AbstractSet[str])
assert_type(state(factory=lambda: {"a": 1}), Mapping[str, int])
assert_type(state(factory=lambda: ["a"]), Sequence[str])
assert_type(state(factory=lambda: {"a"}), AbstractSet[str])
assert_type(state(("a",)), tuple[str])  # pyright: ignore[reportAssertTypeFailure]  # pyright keeps the element literal here; pyrefly widens it
assert_type(state(frozenset({"a"})), frozenset[str])
assert_type(state(0), int)
assert_type(state(factory=int), int)

assert_type(state({"a": 1}), Mapping[str, int])
assert_type(state(["a"]), Sequence[str])
assert_type(state({"a"}), AbstractSet[str])
assert_type(state(factory=lambda: {"a": 1}), Mapping[str, int])
assert_type(state(factory=lambda: ["a"]), Sequence[str])
assert_type(state(factory=lambda: {"a"}), AbstractSet[str])
assert_type(state(("a",)), tuple[str])  # pyright: ignore[reportAssertTypeFailure]  # pyright keeps the element literal here; pyrefly widens it
assert_type(state(0), int)
assert_type(state(factory=int), int)


class Anonymous(Reactive):
    flag: bool = state(default=False)


class UserSettings(Reactive):
    theme: str = state("system")


assert_type(Anonymous().flag, bool)
assert_type(UserSettings().theme, str)
assert_type(addresses(lambda: UserSettings().theme), tuple[Any, ...])


class ResourceTypes(Component):
    @resource(pending=PendingMode.ATOMIC)
    async def atomic(self) -> int:
        return 1

    @resource
    async def visible(self) -> int:
        return 1

    @override
    def render(self) -> Paragraph:
        return paragraph("resources")


assert_type(ResourceTypes().atomic, AtomicResource[int])
assert_type(ResourceTypes().atomic.status, AtomicResourceStatus[int])
assert_type(ResourceTypes().visible, Resource[int])
assert_type(ResourceTypes().visible.status, ResourceStatus[int])
assert_type(Ready[int](1), Ready[int])
assert_type(Failed[int](ValueError()), Failed[int])
assert_type(Pending[int](), Pending[int])

text_context = ContextKey[str]("text")


class ContextTypes(Component):
    @override
    def render(self):
        assert_type(self.inject(text_context), str)
        assert_type(self.inject(text_context, "fallback"), str)
        return ()
