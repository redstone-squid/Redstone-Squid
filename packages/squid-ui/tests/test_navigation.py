"""Page-seek option selection: what a jump control offers when there are too many pages."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid_ui.planning.navigation import SEEK_OPTION_LIMIT, _seek_pages


@pytest.mark.parametrize(
    ("page", "extent"),
    [(0, 2), (0, 25), (12, 26), (0, 200), (137, 200), (199, 200), (500, 5000)],
)
def test_a_jump_select_always_fits_and_always_offers_the_visible_page(page: int, extent: int) -> None:
    pages = _seek_pages(page, extent)

    assert len(pages) <= SEEK_OPTION_LIMIT
    assert pages == sorted(set(pages))
    assert page in pages
    assert pages[0] == 0
    assert pages[-1] == extent - 1


@given(extent=st.integers(min_value=1, max_value=10_000), offset=st.integers(min_value=0))
def test_the_same_four_properties_hold_for_any_page_of_any_extent(extent: int, offset: int) -> None:
    """The parametrized cases above are the interesting shapes; these are the law.

    Discord caps a select at 25 options, so a jump control over 5,000 pages has to choose.
    Whatever it chooses must fit, must be strictly increasing, must include the page the
    reader is on -- otherwise the control cannot show its own state -- and must reach both
    ends, or some pages become unreachable.
    """
    page = offset % extent

    pages = _seek_pages(page, extent)

    assert len(pages) <= SEEK_OPTION_LIMIT
    assert pages == sorted(set(pages))
    assert page in pages
    assert pages[0] == 0
    assert pages[-1] == extent - 1
