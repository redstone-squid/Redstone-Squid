"""Smoke contracts for the machines the conformance suite cannot reach.

`test_machine_conformance.py` parametrizes every law over the eight pure `StateMachine`s. The
four below are not state machines -- they are components backed by a resource or keyed by
actor -- so they have no `initial_state` to enumerate from, and this file keeps the one claim
that still applies to them: they render.
"""

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine


async def _finished(_event: sp.TransitionEvent[sp.DecisionState]) -> None:
    return None


async def test_resource_backed_and_actor_keyed_widgets_render_without_a_frontend() -> None:
    source = sl.sources.list_source(("one", "two"))

    async def picked(_event: sl.interactions.ActionEvent, _items: tuple[str, ...]) -> None:
        return None

    agreement = sp.Agreement("Approve?", (sp.AgreementParticipant("one", "One"),))
    browser = sp.Browser(source, identity=str, label=str, detail=lambda item: item)
    search = sp.SearchPicker(lambda _query: source, identity=str, label=str, on_pick=picked)
    ranking = sp.SourceRankedList(source, key="ranking", page_size=10, identity=str, label=str)

    for component in (agreement, browser, search, ranking):
        assert engine.render_tree(component), f"{type(component).__name__} rendered nothing"


def test_confirm_builds_a_component_that_renders() -> None:
    assert engine.render_tree(sp.confirm("Confirm?", on_confirm=_finished))
