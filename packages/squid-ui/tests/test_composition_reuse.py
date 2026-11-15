"""Structural hints never retain handlers or stand in for current certification."""

from dataclasses import replace

import pytest

import squid_ui as sl
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.cache import PlanCache
from squid_ui.planning.identity import stable_fingerprint
from squid_ui.planning.navigation import TurnPage, default_nav
from squid_ui.planning.result import PlanReuse
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter
from squid_ui_discord.limits import V2Limits
from squid_ui_discord.planning.discord import components_v2_target

V2 = components_v2_target(AdapterProfile(DiscordAdapter, "reuse", ">=1"))
REGION = OccurrenceId(("root", "r"))
OPTIONS = CompositionOptions(automatic=True, partitions={REGION: (2, 4)})


def content() -> sl.semantic.Region:
    return sl.region(*(sl.paragraph(key, key=key) for key in "abcd"), key="r", title="R")


def test_partition_store_is_bounded_and_immutable() -> None:
    cache = PlanCache(capacity=2)
    cuts = {REGION: (2, 4)}
    cache.remember_partitions("a", cuts)
    cuts.clear()
    cache.remember_partitions("b", {})
    assert cache.partition_hint("a") == {REGION: (2, 4)}
    cache.remember_partitions("c", {})
    assert cache.partition_hint("b") is None


def test_cross_owner_hint_rebuilds_callbacks() -> None:
    async def first(event: sl.PageEvent) -> None:
        pass

    async def second(event: sl.PageEvent) -> None:
        pass

    cache = PlanCache()
    old = plan(
        replace(content(), page=sl.controlled(0, first)), target=V2, cache=cache, nav=default_nav, composition=OPTIONS
    )
    new = plan(
        replace(content(), page=sl.controlled(0, second)), target=V2, cache=cache, nav=default_nav, composition=OPTIONS
    )
    assert new.metrics.partition_hint
    assert new.metrics.target_evaluations > 0
    assert new.allocation is not old.allocation
    binding = new.bindings["__cursor_next.$/r"]
    assert isinstance(binding.handler, TurnPage)
    assert isinstance(binding.handler.commit.owner, sl.ownership.Controlled)
    assert binding.handler.commit.owner.on_change is second
    assert new.report.search is not None and new.report.search.optimal
    assert new.scene == old.scene


@pytest.mark.parametrize("cuts", [(1, 4), (5,), (4, 2), (0, 2, 4), ()])
def test_stale_hints_cannot_override_fixed_partitions(cuts: tuple[int, ...]) -> None:
    cache = PlanCache()
    document = sl.Document((content(),))
    key = stable_fingerprint((document, replace(OPTIONS, pages={})))
    cache.remember_partitions(key, {REGION: cuts})
    result = plan(document, target=V2, cache=cache, composition=OPTIONS)
    assert result.metrics.partition_hint
    assert result.allocation is not None
    assert result.allocation.regions[REGION].cuts == (2, 4)


def test_changed_limits_recertify_hint_and_content_misses() -> None:
    cache = PlanCache()
    plan(content(), target=V2, cache=cache, composition=OPTIONS, nav=default_nav)
    with pytest.raises(sl.planning.JointInfeasibleError):
        plan(
            content(),
            target=replace(V2, limits=V2Limits(total_components=1)),
            cache=cache,
            composition=OPTIONS,
            nav=default_nav,
        )
    changed = replace(content(), title="Changed")
    result = plan(changed, target=V2, cache=cache, composition=OPTIONS)
    assert not result.metrics.partition_hint


def test_retained_metrics_describe_this_call_and_keep_certificate() -> None:
    session = PresentationState()
    result = plan(content(), target=V2, session=session, composition=OPTIONS)
    session.apply_updates(result.session_updates)
    for selected in (result.for_page({REGION: 1}), plan(content(), target=V2, session=session, composition=OPTIONS)):
        assert selected.metrics.reuse == PlanReuse.ALLOCATION
        assert selected.metrics.cache_hit
        assert selected.metrics.work_used == selected.metrics.target_evaluations == 0
        assert selected.metrics.generated_states == selected.metrics.certified_combinations == 0
        assert selected.report.search == result.report.search


def test_hint_search_remains_bounded() -> None:
    cache = PlanCache()
    options = replace(OPTIONS, work_limit=1)
    document = sl.Document((content(),))
    cache.remember_partitions(stable_fingerprint((document, options)), {REGION: (2, 4)})
    with pytest.raises(sl.planning.JointExhaustedError) as error:
        plan(document, target=V2, cache=cache, composition=options)
    assert error.value.diagnostics.work_used <= 1


def test_route_factory_external_state_is_fresh() -> None:
    prefix = "old"

    def route(page: int) -> str:
        return f"{prefix}:{page}"

    cache = PlanCache()
    session = PresentationState()
    document = replace(content(), page=sl.RoutedPage(0, route))
    first = plan(document, target=V2, cache=cache, session=session, composition=OPTIONS, nav=default_nav)
    session.apply_updates(first.session_updates)
    prefix = "new"
    second = plan(document, target=V2, cache=cache, session=session, composition=OPTIONS, nav=default_nav)
    assert second.allocation is not first.allocation
    assert not second.metrics.partition_hint
    assert "new:" in sl.scene.Codec.dumps(second.scene)
    assert "old:" not in sl.scene.Codec.dumps(second.scene)


def test_hint_preserves_complete_search_winner() -> None:
    cache = PlanCache()
    document = sl.Document((content(),))
    options = CompositionOptions(automatic=True)
    expected = plan(document, target=V2, composition=options)
    cache.remember_partitions(stable_fingerprint((document, options)), {REGION: (1, 2, 3, 4)})
    hinted = plan(document, target=V2, cache=cache, composition=options)
    assert hinted.report.search is not None and hinted.report.search.optimal
    assert expected.allocation is not None and hinted.allocation is not None
    assert hinted.allocation.regions[REGION].cuts == expected.allocation.regions[REGION].cuts
    assert hinted.scene == expected.scene
