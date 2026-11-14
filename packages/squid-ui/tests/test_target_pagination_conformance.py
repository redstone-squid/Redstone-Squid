"""Every document surface participates in the same allocation and delivery contract."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import classic_target, components_v2_target
from squid_ui.planning.limits import ClassicLimits
from squid_ui.planning.navigation import default_nav
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "conformance.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "conformance.slack", "1")
TARGETS = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    sl.html.target(),
    sl.slack.message_target(adapter=SLACK),
    sl.slack.home_target(adapter=SLACK),
]


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_independent_pages_preserve_content_and_reuse_delivered_allocation(target: Target[Any, Any, Any, Any]) -> None:
    document = sl.group(
        *(
            sl.region(*(sl.paragraph(f"{key}{i}", key=str(i)) for i in range(2)), key=key, title=key)
            for key in ("left", "right")
        ),
        key="body",
    )
    left = OccurrenceId(("root", "body", "left"))
    right = OccurrenceId(("root", "body", "right"))
    options = CompositionOptions(automatic=True, partitions={left: (1, 2), right: (1, 2)})
    session = PresentationState()
    first = plan(document, target=target, composition=options, session=session, nav=default_nav)
    assert first.allocation is not None
    for a in range(2):
        for b in range(2):
            page = first.allocation.select({left: a, right: b})
            wire = scene.Codec.dumps(page.scene)
            assert f"left{a}" in wire and f"right{b}" in wire
            assert f"left{1 - a}" not in wire and f"right{1 - b}" not in wire
    assert session.retained_composition is None
    session.apply_updates(first.session_updates)
    repeated = plan(document, target=target, composition=options, session=session, nav=default_nav)
    assert repeated.allocation is first.allocation
    assert repeated.metrics.work_used == 0


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_production_list_uses_shared_search(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.semantic.List(
        tuple(sl.semantic.ListItem(str(i), f"item {i}") for i in range(3)), key="items", page_size=1
    )
    result = plan(node, target=target, nav=default_nav)
    assert result.allocation is not None
    assert result.report.legacy_pagination is None
    assert result.scene.pagers[0].pages == 3


def test_classic_content_and_embed_pools_are_independent() -> None:
    target = replace(classic_target(DISCORD), limits=ClassicLimits(content=8, embed_text=20))
    result = plan(
        sl.group(sl.paragraph("outside"), sl.block(sl.paragraph("inside"))),
        target=target,
        composition=CompositionOptions(automatic=True),
    )
    body = result.scene.body
    assert isinstance(body, scene.ClassicMessage)
    assert body.content == "outside"
    assert body.embeds[0].description == "inside"
