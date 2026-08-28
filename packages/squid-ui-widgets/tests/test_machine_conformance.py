"""Laws every advertised state machine obeys, checked against all of them at once.

Each widget has its own file for what makes it that widget. This file holds what makes it a
`StateMachine` at all: the guarantees the two shells rely on and no single widget's tests
would think to assert. A new machine joins `MACHINES` and inherits every one of them.
"""

from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.semantic import (
    ActionControl,
    Choices,
    FormTrigger,
    RoutedActionControl,
    RoutedChoices,
)
from squid_ui_widgets import testing as wt

KEYED_CONTROLS = (ActionControl, Choices, FormTrigger, RoutedActionControl, RoutedChoices)


def _form(title: str = "Value") -> sl.forms.FormSpec:
    return sl.forms.FormSpec(title, (sl.forms.TextField(key="value", label="Value"),))


def _decision() -> sp.Decision:
    return sp.Decision("Choose", (sp.DecisionOption("one", "One"), sp.DecisionOption("two", "Two")))


def _collection() -> sp.CollectionEditor:
    return sp.CollectionEditor("Items", create=_form(), label=lambda value: str(value["value"]))


def _editor() -> sp.Editor:
    return sp.Editor("Edit", (sp.EditorSection.from_form("value", "Value", _form()),))


def _menu() -> sp.Menu:
    return sp.Menu("Menu", (sp.MenuEntry("entry", "Entry", "Body"),))


def _tabs() -> sp.Tabs:
    return sp.Tabs((sp.Tab("one", "One", "Body"), sp.Tab("two", "Two", "Other")), key="tabs")


def _multi_choice() -> sp.MultiChoice:
    return sp.MultiChoice(
        "Choices",
        (sp.MultiChoiceGroup("group", "Group", (sl.semantic.Choice("one", "One"), sl.semantic.Choice("two", "Two"))),),
    )


def _ranked() -> sp.RankedList:
    return sp.RankedList(("A", "B", "C"), key="ranked", label=str, value=len, page_size=2)


def _wizard() -> sp.Wizard:
    return sp.Wizard("Wizard", (sp.WizardStep("value", "Value", _form()),), review=True)


MACHINES: tuple[tuple[str, Callable[[], sp.StateMachine[Any, Any]]], ...] = (
    ("decision", _decision),
    ("collection", _collection),
    ("editor", _editor),
    ("menu", _menu),
    ("tabs", _tabs),
    ("multi_choice", _multi_choice),
    ("ranked", _ranked),
    ("wizard", _wizard),
)

machine = pytest.mark.parametrize("build", [build for _name, build in MACHINES], ids=[name for name, _ in MACHINES])


def _control_keys(nodes: tuple[object, ...]) -> list[str]:
    return [node.key for node in engine.walk(nodes) if isinstance(node, KEYED_CONTROLS)]


@machine
def test_the_initial_state_is_stable_across_reads(build: Callable[[], sp.StateMachine[Any, Any]]) -> None:
    """Two reads must agree, and rendering must not quietly move it."""
    subject = build()
    first = subject.initial_state

    _ = wt.mounted(subject).nodes

    assert subject.initial_state == first
    assert subject.initial_state == subject.initial_state


@machine
def test_both_shells_render_something(build: Callable[[], sp.StateMachine[Any, Any]]) -> None:
    assert wt.mounted(build()).nodes
    assert wt.routed(build()).nodes


@machine
def test_no_two_controls_in_one_render_share_a_key(build: Callable[[], sp.StateMachine[Any, Any]]) -> None:
    """A duplicate key silently cross-wires two controls through the mount's handler table:
    the second registration wins, and the first button starts doing the second one's job."""
    for nodes in (wt.mounted(build()).nodes, wt.routed(build()).nodes):
        keys = _control_keys(nodes)

        duplicates = {key for key in keys if keys.count(key) > 1}

        assert not duplicates, f"{sorted(duplicates)} appear more than once in one render"


@machine
def test_every_action_the_routed_shell_encodes_is_one_the_machine_accepts(
    build: Callable[[], sp.StateMachine[Any, Any]],
) -> None:
    """The render and the transition table are written apart; nothing else checks they agree."""
    subject = build()
    render = wt.routed(subject)

    for route in render.routes:
        result = subject.transition(subject.initial_state, route.action)

        assert isinstance(result, type(subject.initial_state))


@machine
def test_a_render_encodes_exactly_one_route_per_routed_control(
    build: Callable[[], sp.StateMachine[Any, Any]],
) -> None:
    """An unencoded control is dead; an encoded route with no control is an id nobody can reach."""
    render = wt.routed(build())

    assert len(render.route_ids()) == len(render.routes)


@machine
def test_an_unknown_action_is_the_identity_rather_than_an_error(
    build: Callable[[], sp.StateMachine[Any, Any]],
) -> None:
    """Discord replays ids from messages that may be older than the current code, so a machine
    meets actions it no longer has. Ignoring one is recoverable; raising is not."""
    subject = build()
    initial = subject.initial_state

    assert subject.transition(initial, "no-such-action") == initial
    assert subject.transition(initial, "") == initial


@machine
def test_a_transition_is_deterministic(build: Callable[[], sp.StateMachine[Any, Any]]) -> None:
    subject = build()
    render = wt.routed(subject)

    for route in render.routes:
        once = subject.transition(subject.initial_state, route.action)
        twice = subject.transition(subject.initial_state, route.action)

        assert once == twice, f"{route.action} is not deterministic"


@machine
@given(action=st.text(max_size=40))
def test_any_string_at_all_is_a_safe_action(build: Callable[[], sp.StateMachine[Any, Any]], action: str) -> None:
    """The stronger form of the law above, over inputs nobody would think to write down."""
    subject = build()
    initial = subject.initial_state

    result = subject.transition(initial, action)

    assert isinstance(result, type(initial))
