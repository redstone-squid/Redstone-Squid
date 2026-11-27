"""Page assembly from in-memory rows, and cursor tokens."""

from uuid import UUID

import pytest
from whenever import Instant

from squid.core.pagination import InvalidPageCursorError, PageAnchor, PageCursor, offset_page


def test_offset_page_addresses_both_neighbours_by_offset() -> None:
    page = offset_page([9, 8, 7, 6, 5], offset=2, page_size=2)

    assert page.items == (7, 6)
    assert page.total == 5
    assert page.next == PageAnchor(offset=4)
    assert page.prev == PageAnchor(offset=0)


def test_offset_page_stops_at_either_end() -> None:
    first = offset_page([9, 8, 7], offset=0, page_size=2)
    last = offset_page([9, 8, 7], offset=2, page_size=2)

    assert (first.prev, first.next) == (None, PageAnchor(offset=2))
    assert (last.prev, last.next) == (PageAnchor(offset=0), None)


def test_offset_page_with_a_total_takes_rows_that_start_at_the_offset() -> None:
    page = offset_page([7, 6], offset=2, page_size=2, total=5)

    assert page.items == (7, 6)
    assert page.next == PageAnchor(offset=4)


@pytest.mark.parametrize(
    "cursor",
    [
        PageCursor("newest", Instant.from_utc(2026, 11, 20, 14, 3, 11, nanosecond=482913000), 812),
        PageCursor("title", "3x3 piston door", 812, backward=True),
        PageCursor("fastest_open", None, 812),
        PageCursor("oldest", 6, UUID("6f1c2a52-3b5e-4c55-9a41-9d1f4e0b7d10")),
    ],
)
def test_a_cursor_survives_its_token(cursor: PageCursor) -> None:
    assert PageCursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize("token", ["", "not base64!", "bnVsbA", PageCursor("id", 1, 1).encode()[:-3]])
def test_a_damaged_token_is_rejected(token: str) -> None:
    with pytest.raises(InvalidPageCursorError):
        PageCursor.decode(token)


def test_a_token_with_a_mistyped_id_is_rejected() -> None:
    token = PageCursor("id", 1, 1).encode()
    forged = PageCursor.decode(token)
    object.__setattr__(forged, "id", True)

    with pytest.raises(InvalidPageCursorError):
        PageCursor.decode(forged.encode())
