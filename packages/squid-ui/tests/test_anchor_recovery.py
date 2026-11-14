"""Reading positions follow surviving content using only committed history."""

import pytest

from squid_ui.chrome import DEFAULT_CHROME
from squid_ui.pages import PagePosition
from squid_ui.planning.cursors import CursorCoordinator, MaterializedCursorRequest, recover_anchor
from squid_ui.presentation import CursorState, PresentationState


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        ({"c": 3, "d": 0}, "c"),
        ({"b": 2, "d": 0}, "d"),
        ({"a": 0, "e": 1}, "e"),
        ({"b": 0, "e": 1}, "b"),
        ({"a": 0}, "a"),
        ({"new": 0}, None),
        ({}, None),
    ],
)
def test_recovery_uses_old_order(available: dict[str, int], expected: str | None) -> None:
    assert recover_anchor("c", ("a", "b", "c", "d", "e"), available) == expected


def test_recovery_is_staged_and_explicit_navigation_wins() -> None:
    old = CursorState(PagePosition("c", 1), 3, "old", source_order=("a", "b", "c", "d", "e"))
    state = PresentationState(cursors={"list": old})
    request = MaterializedCursorRequest("list", 2, "new", {"d": 0, "b": 1}, source_order=("d", "b"))
    coordinator = CursorCoordinator(state, DEFAULT_CHROME)
    grant = coordinator.grant(request)
    assert grant.position == PagePosition("d", 0)
    coordinator.record(request, grant.position, anchor=grant.position.anchor)
    assert state.cursor("list") == old
    assert CursorCoordinator(state, DEFAULT_CHROME).grant(request) == grant
    override = CursorCoordinator(state, DEFAULT_CHROME, overrides={"list": PagePosition(page=1)})
    assert override.grant(request).position == PagePosition(page=1)
    state.apply_updates(coordinator.updates)
    assert state.cursor("list").source_order == ("d", "b")
    assert state.cursor("list").position == PagePosition("d", 0)
