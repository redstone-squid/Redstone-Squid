"""Standalone contracts for shared reactive namespaces."""

import contextvars

import pytest

from squid_reactivity import LocalTopicBus, ReactiveConflictError, SharedState, state, transaction
from squid_reactivity.core import _CURRENT
from squid_reactivity.topics import Address, CellAddress


class Preferences(SharedState[int]):
    theme: str = state("dark")


def test_shared_commit_publishes_the_exact_cell() -> None:
    bus = LocalTopicBus()
    preferences = Preferences(bus, 7)
    address = CellAddress(preferences, "theme")
    seen: list[Address] = []
    bus.subscribe(address, seen.append)

    with transaction():
        preferences.theme = "light"
        assert seen == []

    assert preferences.theme == "light"
    assert seen == [address]


def test_shared_read_write_conflict_rolls_back() -> None:
    bus = LocalTopicBus()
    preferences = Preferences(bus, 7)

    with pytest.raises(ReactiveConflictError), transaction():
        assert preferences.theme == "dark"
        preferences.theme = "ours"
        descriptor = type(preferences)._state_descriptors["theme"]
        outside = contextvars.copy_context()
        outside.run(_CURRENT.set, None)
        outside.run(descriptor.cell(preferences).write, "theirs")

    assert preferences.theme == "theirs"


def test_shared_namespace_rejects_undeclared_attributes() -> None:
    preferences = Preferences(LocalTopicBus(), 7)

    with pytest.raises(AttributeError, match="not declared state"):
        preferences.extra = True  # type: ignore[attr-defined]
