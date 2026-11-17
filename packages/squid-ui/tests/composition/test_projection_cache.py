"""Page projections reuse work only within the same callback generation."""

import gc
import weakref
from collections.abc import Mapping
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest

import squid_ui as sl
from squid_ui import scene, testing
from squid_ui.ext.discord.planning.discord import components_v2_target
from squid_ui.interactions import PressHandler
from squid_ui.interactions.handlers import FrontendHandler
from squid_ui.planning import CompositionOptions, OccurrenceId, composition_state, plan
from squid_ui.planning.action_projection import ActionAllocation
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.allocation import AllocationSnapshot, CertifiedPage, RegionPages
from squid_ui.planning.resources import EMPTY_COST
from squid_ui.planning.result import PlanResult
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter

TARGET = components_v2_target(AdapterProfile(DiscordAdapter, "projection", ">=1"))
REGION = OccurrenceId(("root", "r"))
OPTIONS = CompositionOptions(automatic=True, partitions={REGION: (1, 2)})


async def ignored(event: sl.PressEvent) -> None:
    pass


async def page_changed(event: sl.PageEvent) -> None:
    pass


def document(
    handler: PressHandler, page: int = 0, *, display: sl.semantic.ControlDisplay = sl.semantic.ControlDisplay.AUTO
) -> sl.Document:
    return sl.Document(
        (
            sl.region(
                sl.action_controls(sl.action_control("Open", handler, key="open"), key="actions", display=display),
                sl.paragraph("Second", key="second"),
                key="r",
                title="Region",
                page=sl.controlled(page, page_changed),
            ),
        )
    )


async def test_page_revisits_keep_projection_but_new_handlers_start_a_fresh_cache() -> None:
    calls: list[str] = []

    async def first(event: sl.PressEvent) -> None:
        calls.append("first")

    async def second(event: sl.PressEvent) -> None:
        calls.append("second")

    session = PresentationState()
    initial = plan(document(ignored), target=TARGET, session=session, composition=OPTIONS)
    session.apply_updates(initial.session_updates)
    current = plan(document(first), target=TARGET, session=session, composition=OPTIONS)
    session.apply_updates(current.session_updates)
    allocation = current.allocation
    assert isinstance(allocation, ActionAllocation)
    page = allocation.select({REGION: 0})
    allocation.select({REGION: 1})
    assert allocation.select({REGION: -1}) is page  # Clamping selects the same certified output.
    assert allocation.cache_snapshot().entries == 2
    assert allocation.cache_snapshot().hits >= 2

    turned = plan(document(first, page=1), target=TARGET, session=session, composition=OPTIONS)
    assert turned.allocation is allocation
    session.apply_updates(turned.session_updates)
    updated = plan(document(second), target=TARGET, session=session, composition=OPTIONS)
    assert isinstance(updated.allocation, ActionAllocation)
    assert updated.allocation is not allocation
    assert updated.allocation.identity == allocation.identity
    assert updated.allocation.source is allocation.source  # Existing slots need no further detachment.
    assert updated.allocation.cache_snapshot().entries == 1
    await updated.bindings["open"].handler(testing.press_event())
    await page.bindings["open"].handler(testing.press_event())
    assert calls == ["second", "first"]


class Outputs:
    """Small allocation fixture with distinct immutable outputs and clamped selection."""

    identity = "fixture"
    regions: Mapping[OccurrenceId, RegionPages] = {}

    def __init__(self, result: PlanResult[scene.ComponentsV2]) -> None:
        self.outputs = tuple(replace(result) for _ in range(3))

    def visible(self, coordinates: Mapping[OccurrenceId, int]) -> Mapping[OccurrenceId, int]:
        return {REGION: max(0, min(2, coordinates.get(REGION, 0)))}

    def select(self, coordinates: Mapping[OccurrenceId, int] | None = None) -> PlanResult[scene.ComponentsV2]:
        return self.outputs[self.visible(coordinates or {})[REGION]]


def test_projection_lru_evicts_and_recomputes_without_mutating_source() -> None:
    original = plan(sl.action_controls(sl.action_control("Open", ignored, key="open"), key="actions"), target=TARGET)
    controls: dict[str, sl.semantic.ActionControl[Any]] = {"open": sl.action_control("Open", ignored, key="open")}
    source = Outputs(original)
    allocation = ActionAllocation(source, controls, "projection", capacity=2)
    controls.clear()
    first = allocation.select({REGION: 0})
    second = allocation.select({REGION: 1})
    assert allocation.select({REGION: 0}) is first
    allocation.select({REGION: 2})
    assert allocation.cache_snapshot().evictions == 1
    assert allocation.select({REGION: 1}) is not second
    assert allocation.cache_snapshot().entries == 2
    assert allocation.cache_snapshot().evictions == 2
    assert source.select({REGION: 0}).bindings is original.bindings
    assert "open" in allocation.controls


def test_failed_binding_does_not_cache_a_projection() -> None:
    original = plan(sl.action_controls(sl.action_control("Open", ignored, key="open"), key="actions"), target=TARGET)
    controls = {"open": sl.action_control("Open", FrontendHandler("different", ignored), key="open")}
    allocation = ActionAllocation(Outputs(original), controls, "projection")
    for _ in range(2):
        with pytest.raises(TypeError, match="cannot be bound"):
            allocation.select()
    assert allocation.cache_snapshot().entries == 0
    assert allocation.cache_snapshot().misses == 2


def test_rebinding_prepares_the_authored_snapshot_once() -> None:
    async def changed(event: sl.PressEvent) -> None:
        pass

    session = PresentationState()
    first = plan(document(ignored), target=TARGET, session=session, composition=OPTIONS)
    session.apply_updates(first.session_updates)
    with (
        patch.object(composition_state, "semantic_graph", wraps=composition_state.semantic_graph) as graph,
        patch.object(
            composition_state, "stable_fingerprint", wraps=composition_state.stable_fingerprint
        ) as fingerprint,
    ):
        current_document = document(changed)
        current = plan(current_document, target=TARGET, session=session, composition=OPTIONS)
        assert graph.call_count == 1
        assert fingerprint.call_count == 2  # One Document and one region content fingerprint.
        session.apply_updates(current.session_updates)
        retained = session.retained_composition
        assert retained is not None
        current.for_page({REGION: 1})
        replay = plan(current_document, target=TARGET, session=session, composition=OPTIONS)
        assert graph.call_count == 1
        assert fingerprint.call_count == 2  # One Document and one region content fingerprint.
        assert replay.allocation is current.allocation
        assert session.retained_composition is retained


@pytest.mark.parametrize("display", [sl.semantic.ControlDisplay.INDIVIDUAL, sl.semantic.ControlDisplay.GROUPED])
async def test_reused_layout_releases_superseded_callback_receivers(display: sl.semantic.ControlDisplay) -> None:
    class Receiver:
        def __init__(self, name: str) -> None:
            self.name = name

        async def handle(self, event: sl.PressEvent) -> None:
            calls.append(self.name)

    calls: list[str] = []
    session = PresentationState()
    original = Receiver("old")
    reference = weakref.ref(original)
    initial = plan(document(original.handle, display=display), target=TARGET, session=session, composition=OPTIONS)
    session.apply_updates(initial.session_updates)
    replacement = Receiver("new")
    candidate = plan(document(replacement.handle, display=display), target=TARGET, session=session, composition=OPTIONS)
    # An undelivered candidate cannot retire the displayed generation.
    del original
    gc.collect()
    assert reference() is not None
    initial_binding = next(binding for key, binding in initial.bindings.items() if not key.startswith("__cursor"))
    event = testing.selection_event("open") if initial_binding.routes else testing.press_event()
    await initial_binding.handler(event)
    del initial_binding
    session.apply_updates(candidate.session_updates)
    del initial
    gc.collect()
    assert reference() is None
    binding = next(binding for key, binding in candidate.bindings.items() if not key.startswith("__cursor"))
    await binding.handler(event)
    assert calls == ["old", "new"]


async def test_grouped_callbacks_with_repeated_local_keys_keep_their_scope() -> None:
    called: list[str] = []

    def grouped(version: str) -> sl.Document:
        def group(name: str) -> sl.semantic.ActionControls:
            async def handler(event: sl.PressEvent) -> None:
                called.append(f"{version}:{name}")

            return sl.action_controls(
                sl.action_control("Run", handler, key="run"), key=name, display=sl.semantic.ControlDisplay.GROUPED
            )

        return sl.Document((group("left"), group("right")))

    session = PresentationState()
    initial = plan(grouped("old"), target=TARGET, session=session)
    session.apply_updates(initial.session_updates)
    current = plan(grouped("new"), target=TARGET, session=session)
    assert current.metrics.target_evaluations == 0
    assert len(current.bindings) == 2
    for binding in current.bindings.values():
        assert "run" in binding.routes
        await binding.handler(testing.selection_event("run"))
    assert sorted(called) == ["new:left", "new:right"]


def test_detached_allocation_preserves_nested_coordinate_selection() -> None:
    outer = OccurrenceId(("outer",))
    inner = OccurrenceId(("outer", "inner"))
    regions = {
        identity: RegionPages(str(identity), (1, 2), (frozenset(), frozenset()), EMPTY_COST, ((), ()))
        for identity in (outer, inner)
    }
    snapshot = AllocationSnapshot(
        "certificate",
        regions,
        (
            CertifiedPage(((outer, 0),), "outer-only"),
            CertifiedPage(((outer, 1), (inner, 0)), "inner-first"),
            CertifiedPage(((outer, 1), (inner, 1)), "inner-last"),
        ),
    )
    assert snapshot.select({outer: -3, inner: 1}) == "outer-only"
    assert snapshot.visible({outer: 0, inner: 1}) == {outer: 0}
    assert snapshot.select({outer: 1}) == "inner-first"
    assert snapshot.select({outer: 99, inner: 99}) == "inner-last"
    assert snapshot.visible({outer: 99, inner: 99}) == {outer: 1, inner: 1}
    with pytest.raises(sl.LayoutInvariantError, match="unknown region"):
        snapshot.select({OccurrenceId(("unknown",)): 0})


async def test_initial_live_templates_do_not_retain_action_receivers() -> None:
    class Receiver:
        async def handle(self, event: sl.PressEvent) -> None:
            pass

    receiver = Receiver()
    reference = weakref.ref(receiver)
    session = PresentationState()
    result = plan(document(receiver.handle), target=TARGET, session=session, composition=OPTIONS)
    assert isinstance(result.allocation, ActionAllocation)
    templates = result.allocation.source
    assert isinstance(templates, AllocationSnapshot)
    del result, receiver, session
    gc.collect()
    assert reference() is None
    with pytest.raises(sl.LayoutInvariantError, match="template must be bound"):
        await templates.select({REGION: 0}).bindings["open"].handler(testing.press_event())


async def test_live_templates_preserve_selected_fallback_handlers() -> None:
    calls: list[str] = []

    async def primary(event: sl.PressEvent) -> None:
        calls.append("primary")

    async def replacement(event: sl.PressEvent) -> None:
        calls.append("replacement")

    async def ordinary(event: sl.PressEvent) -> None:
        calls.append("ordinary")

    node = sl.Document(
        (
            sl.fallback(
                sl.action_controls(sl.action_control("Primary", primary, key="action"), key="actions"),
                sl.action_controls(sl.action_control("Alternate", replacement, key="action"), key="actions"),
            ),
            sl.action_controls(sl.action_control("Other", ordinary, key="other"), key="other-actions"),
        )
    )
    options = CompositionOptions(automatic=True, selections={OccurrenceId(("root", "@0")): "fallback-1"})
    session = PresentationState()
    result = plan(node, target=TARGET, session=session, composition=options)
    assert isinstance(result.allocation, ActionAllocation)
    assert set(result.allocation.controls) == {"other"}
    for binding in result.bindings.values():
        await binding.handler(testing.press_event())
    assert sorted(calls) == ["ordinary", "replacement"]
