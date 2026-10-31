"""Page assembly: overfetch trimming and the anchors each direction produces."""

from dataclasses import dataclass

from squid.core.pagination import FIRST_PAGE, PageAnchor, PageSelector, keyset_page, offset_page


@dataclass(frozen=True, slots=True)
class Row:
    id: int


def rows(*ids: int) -> list[Row]:
    return [Row(identifier) for identifier in ids]


def assemble(page_rows: list[Row], *, selector: PageSelector = FIRST_PAGE, page_size: int = 2, total: int = 9):
    return keyset_page(
        page_rows,
        selector=selector,
        page_size=page_size,
        total=total,
        keyset=True,
        id_of=lambda row: row.id,
    )


def test_offset_page_addresses_both_neighbours_by_offset() -> None:
    page = offset_page(rows(9, 8, 7, 6, 5), offset=2, page_size=2)

    assert [row.id for row in page.items] == [7, 6]
    assert page.total == 5
    assert page.next == PageAnchor(offset=4)
    assert page.prev == PageAnchor(offset=0)


def test_offset_page_stops_at_either_end() -> None:
    first = offset_page(rows(9, 8, 7), offset=0, page_size=2)
    last = offset_page(rows(9, 8, 7), offset=2, page_size=2)

    assert (first.prev, first.next) == (None, PageAnchor(offset=2))
    assert (last.prev, last.next) == (PageAnchor(offset=0), None)


def test_the_first_page_offers_no_way_back() -> None:
    page = assemble(rows(9, 8, 7))

    assert [row.id for row in page.items] == [9, 8]
    assert page.next == PageAnchor(after_id=8)
    assert page.prev is None


def test_a_short_first_page_ends_the_collection() -> None:
    page = assemble(rows(9))

    assert page.next is None
    assert page.prev is None


def test_a_forward_page_can_always_go_back_where_it_came_from() -> None:
    page = assemble(rows(7, 6, 5), selector=PageSelector(after_id=8))

    assert [row.id for row in page.items] == [7, 6]
    assert page.next == PageAnchor(after_id=6)
    assert page.prev == PageAnchor(before_id=7)


def test_a_backward_page_trims_its_overfetch_from_the_front() -> None:
    # A `before_id=5` query walks away from the anchor and is flipped back, so the proof that an
    # earlier page exists arrives at the head of the rows rather than the tail.
    page = assemble(rows(9, 8, 7), selector=PageSelector(before_id=6))

    assert [row.id for row in page.items] == [8, 7]
    assert page.prev == PageAnchor(before_id=8)
    assert page.next == PageAnchor(after_id=7)


def test_a_backward_page_at_the_top_offers_no_earlier_page() -> None:
    page = assemble(rows(9, 8), selector=PageSelector(before_id=7))

    assert [row.id for row in page.items] == [9, 8]
    assert page.prev is None
    assert page.next == PageAnchor(after_id=8)


def test_an_offset_addressed_request_is_answered_with_offsets() -> None:
    page = assemble(rows(7, 6, 5), selector=PageSelector(offset=4))

    assert page.next == PageAnchor(offset=6)
    assert page.prev == PageAnchor(offset=2)


def test_an_unanchorable_order_is_answered_with_offsets() -> None:
    page = keyset_page(
        rows(9, 8, 7),
        selector=FIRST_PAGE,
        page_size=2,
        total=9,
        keyset=False,
        id_of=lambda row: row.id,
    )

    assert page.next == PageAnchor(offset=2)
    assert page.prev is None
