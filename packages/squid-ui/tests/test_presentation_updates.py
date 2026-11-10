"""Delivered planner updates advance presentation state as one revision."""

from squid_ui.presentation import ActivePagers, CursorState, CursorUpdate, PresentationState, ToggleState, ToggleUpdate
from squid_ui.sources import Position


def test_delivered_updates_advance_one_revision_and_replay_is_unchanged() -> None:
    state = PresentationState()
    updates = (
        CursorUpdate("pages", CursorState(Position(offset=2), extent=4, fingerprint="content")),
        ToggleUpdate("expanded", ToggleState(on=True)),
    )

    state.apply_updates(updates)

    assert state.cursor("pages").position.offset == 2
    assert state.toggle("expanded").on
    assert state.revision == 1

    state.apply_updates(updates)
    assert state.revision == 1


def test_update_order_controls_which_cursors_survive_delivery() -> None:
    state = PresentationState()
    cursor = CursorState(Position(offset=1), extent=2)

    state.apply_updates((CursorUpdate("old", cursor), ActivePagers(frozenset()), CursorUpdate("new", cursor)))

    assert state.cursors == {"new": cursor}
    assert state.revision == 1
