"""Layout facts preserve source types, fragment coverage, and capture identity."""

import pytest

import squid_ui as sl
import squid_ui.ext.html as html
from squid_ui.authoring.semantic import ListItem
from squid_ui.ext.discord import testing as discord_testing
from squid_ui.planning import plan
from squid_ui.selectors import select


def test_complete_snapshot_reports_pages_queries_and_all_locations() -> None:
    document = sl.bullets(*(sl.bullet(str(i), key=f"item-{i}") for i in range(23)), key="results", group_size=10)
    result = plan(document, target=html.target(), inspect_layout=True, session=sl.runtime.PresentationState())
    ctx = result.layout
    assert ctx is not None
    region = ctx.region(sl.region_ref("results"))
    assert region.count == sl.Known(3)
    assert region.groups == sl.Known(3)
    assert ctx.authored.query(select(ListItem)).count() == 23
    assert ctx.visible.query(select(ListItem)).count() == 10
    positions = ctx.locate(sl.element_ref(ListItem, "item-21"))
    assert positions.complete
    assert len(positions.fragments) == 1
    location = positions.fragments[0]
    assert location.page.coordinates[-1].number == sl.Known(3)
    assert not location.visible
    later = result.for_page({region.identity: 2})
    assert later.layout is not None
    assert later.layout.visible.query(select(ListItem)).count() == 3
    assert later.layout.locate(sl.element_ref(ListItem, "item-21")).fragments[0].visible
    old_match = ctx.authored.query(select(ListItem).key("item-21")).one()
    assert ctx.locate(old_match) == positions
    with pytest.raises(ValueError, match="different tree index"):
        later.layout.locate(old_match)
    other = plan(document, target=html.target(), inspect_layout=True)
    assert other.layout is not None
    with pytest.raises(ValueError, match="different tree index"):
        other.layout.locate(old_match)
    assert ctx.locate(sl.element_ref(ListItem, "absent")) == sl.Locations((), complete=True)


def test_fragmented_source_is_one_query_match_with_several_locations() -> None:
    result = plan(
        sl.bullets(sl.bullet("x" * 6000, key="long"), key="results"),
        target=discord_testing.target_profile("fragments"),
        inspect_layout=True,
    )
    assert result.layout is not None
    ctx = result.layout
    assert ctx.authored.query(select(ListItem)).count() == 1
    locations = ctx.locate(sl.element_ref(ListItem, "long"))
    assert locations.complete and len(locations.fragments) > 1
    indices = {loc.page.coordinates[-1].index.require() for loc in locations.fragments}
    assert len(indices) > 1
    assert isinstance(ctx.region(sl.region_ref("results")).groups, sl.Unknown)


def test_unsupported_exact_facts_fail_explicitly() -> None:
    fact = sl.Unknown(sl.UnknownReason.UNLOADED, "item details have not loaded")
    with pytest.raises(sl.FactUnavailableError, match="item details") as error:
        fact.require()
    assert error.value.fact is fact
    assert sl.Known(None).require() is None


def test_empty_grouped_flow_has_no_selected_content_page() -> None:
    result = plan(sl.bullets(key="empty", group_size=10), target=html.target(), inspect_layout=True)
    assert result.layout is not None
    facts = result.layout.region(sl.region_ref("empty"))
    assert facts.groups == sl.Known(0)
    assert facts.count == sl.Known(0)
    assert facts.selection == sl.Known(None)
