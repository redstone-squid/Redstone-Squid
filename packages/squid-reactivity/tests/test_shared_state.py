"""Standalone contracts for shared reactive namespaces."""

import contextvars

import pytest

from squid_reactivity import (
    Reactive,
    ReactiveConflictError,
    SubscriptionReconciler,
    UndeclaredStateError,
    state,
    transaction,
)
from squid_reactivity.topics import Address, CellAddress
from squid_reactivity.tracking import (
    _CURRENT,
)


class Preferences(Reactive):
    theme: str = state("dark")


def test_shared_commit_publishes_the_exact_cell() -> None:
    preferences = Preferences()
    address = CellAddress(preferences, "theme")
    seen: list[Address] = []
    subscriptions = SubscriptionReconciler(None, seen.append)
    subscriptions.stage([address])
    subscriptions.commit()

    with transaction():
        preferences.theme = "light"
        assert seen == []

    assert preferences.theme == "light"
    assert seen == [address]


def test_shared_read_write_conflict_rolls_back() -> None:
    preferences = Preferences()

    with pytest.raises(ReactiveConflictError), transaction():
        assert preferences.theme == "dark"
        preferences.theme = "ours"
        descriptor = type(preferences)._state_descriptors["theme"]
        # Commit a competing write without joining the transaction being tested.
        outside = contextvars.copy_context()
        outside.run(_CURRENT.set, None)
        outside.run(descriptor.cell(preferences).write, "theirs")

    assert preferences.theme == "theirs"


def test_shared_namespace_rejects_undeclared_attributes() -> None:
    preferences = Preferences()

    with pytest.raises(UndeclaredStateError, match="not declared state"), transaction():
        preferences.extra = True  # type: ignore[attr-defined]
