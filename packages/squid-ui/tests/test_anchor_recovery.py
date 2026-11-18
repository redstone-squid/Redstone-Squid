"""Reading positions follow surviving content using only committed history."""

import pytest

from squid_ui.layout import Known
from squid_ui.pages import PagePosition
from squid_ui.planning.pagination.cursors import CursorCoordinator, MaterializedCursorRequest, recover_anchor
from squid_ui.presentation_state import CursorState, PresentationState


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
    coordinator = CursorCoordinator(state)
    grant = coordinator.grant(request)
    assert grant == PagePosition("d", 0)
    coordinator.record(request, grant, anchor=grant.anchor, count=Known(request.extent))
    assert state.cursor("list") == old
    assert CursorCoordinator(state).grant(request) == grant
    override = CursorCoordinator(state, overrides={"list": PagePosition(page=1)})
    assert override.grant(request) == PagePosition(page=1)
    state.apply_updates(coordinator.updates)
    assert state.cursor("list").source_order == ("d", "b")
    assert state.cursor("list").position == PagePosition("d", 0)


def test_delivery_consumes_only_the_page_request_it_displayed() -> None:
    state = PresentationState()
    state.request_cursor("list", PagePosition(page=1))
    coordinator = CursorCoordinator(state)
    request = MaterializedCursorRequest("list", 3, "content")
    coordinator.record(request, PagePosition(page=1), count=Known(request.extent))
    state.request_cursor("list", PagePosition(page=2))
    state.apply_updates(coordinator.updates)
    assert state.cursor("list").position.page == 1
    assert state.pending_positions == {"list": PagePosition(page=2)}
    next_display = CursorCoordinator(state)
    next_display.record(request, PagePosition(page=2), count=Known(request.extent))
    state.apply_updates(next_display.updates)
    assert not state.pending_positions


def test_resetting_a_pending_page_invalidates_presentation_revision() -> None:
    state = PresentationState()
    state.request_cursor("list", PagePosition(page=1))
    revision = state.revision
    state.reset_cursor()
    assert not state.pending_positions
    assert state.revision > revision
