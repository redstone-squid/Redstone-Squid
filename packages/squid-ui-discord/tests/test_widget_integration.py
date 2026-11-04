"""Widget integration with Discord planning, payload limits, and mounted control keys.

The widget package tests state transitions independently. This shared table checks
representative widget renders and dispatches a usable authored key through the message root.
"""

from collections.abc import Callable

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.semantic import ActionControl, Choices, FormTrigger
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.testing import assert_within_limits, commit_render, interaction_harness

KEYED_CONTROLS = (ActionControl, Choices, FormTrigger)


def _form() -> sl.forms.FormSpec:
    return sl.forms.FormSpec("Value", (sl.forms.TextField(key="value", label="Value"),))


def _decision() -> sl.Component[sl.DiscordTarget]:
    return sp.Decision("Choose", (sp.DecisionOption("one", "One"), sp.DecisionOption("two", "Two"))).build_component()


def _collection() -> sl.Component[sl.DiscordTarget]:
    return sp.CollectionEditor("Items", create=_form(), label=lambda value: str(value["value"])).build_component()


def _editor() -> sl.Component[sl.DiscordTarget]:
    return sp.Editor("Edit", (sp.EditorSection.from_form("value", "Value", _form()),)).build_component()


def _menu() -> sl.Component[sl.DiscordTarget]:
    return sp.Menu("Menu", (sp.MenuEntry("entry", "Entry", "Body"),)).build_component()


def _tabs() -> sl.Component[sl.DiscordTarget]:
    return sp.Tabs((sp.Tab("one", "One", "Body"), sp.Tab("two", "Two", "Other")), key="tabs").build_component()


def _multi_choice() -> sl.Component[sl.DiscordTarget]:
    return sp.MultiChoice(
        "Choices",
        (sp.MultiChoiceGroup("group", "Group", (sl.semantic.Choice("one", "One"), sl.semantic.Choice("two", "Two"))),),
    ).build_component()


def _ranked() -> sl.Component[sl.DiscordTarget]:
    return sp.RankedList(("A", "B", "C"), key="ranked", label=str, value=len, page_size=2).build_component()


def _wizard() -> sl.Component[sl.DiscordTarget]:
    return sp.Wizard("Wizard", (sp.WizardStep("value", "Value", _form()),), review=True).build_component()


def _agreement() -> sl.Component[sl.DiscordTarget]:
    return sp.Agreement("Approve?", (sp.AgreementParticipant("1", "One"),))


WIDGETS: tuple[tuple[str, Callable[[], sl.Component[sl.DiscordTarget]]], ...] = (
    ("decision", _decision),
    ("collection", _collection),
    ("editor", _editor),
    ("menu", _menu),
    ("tabs", _tabs),
    ("multi_choice", _multi_choice),
    ("ranked", _ranked),
    ("wizard", _wizard),
    ("agreement", _agreement),
)

widget = pytest.mark.parametrize("build", [build for _name, build in WIDGETS], ids=[name for name, _ in WIDGETS])


@widget
def test_it_draws_a_discord_view_at_all(build: Callable[[], sl.Component[sl.DiscordTarget]]) -> None:
    message_root = MessageRoot(build(), access=Everyone(), timeout=None)

    assert commit_render(message_root).to_components()


@widget
def test_what_it_draws_is_a_legal_discord_payload(build: Callable[[], sl.Component[sl.DiscordTarget]]) -> None:
    """Check the native payload against Discord limits after planning and drawing."""
    message_root = MessageRoot(build(), access=Everyone(), timeout=None)

    assert_within_limits(commit_render(message_root))


@widget
def test_the_keys_the_machine_chose_are_the_handlers_the_mount_answers_to(
    build: Callable[[], sl.Component[sl.DiscordTarget]],
) -> None:
    """Every registered handler key must come from the authored widget tree.

    The reverse need not hold: a widget may include fallback controls that this render
    does not draw. For example, MultiChoice can author a modal alternative to its picker.
    """
    component = build()
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    commit_render(message_root)

    authored = {node.key for node in engine.walk(engine.render_tree(component)) if isinstance(node, KEYED_CONTROLS)}
    handlers = set(message_root.snapshot().handler_keys)

    assert handlers, "a widget with no handlers would make this vacuous"
    assert handlers <= authored, f"the mount invented {sorted(handlers - authored)}"


@widget
async def test_one_interaction_through_the_real_funnel_reaches_a_handler(
    build: Callable[[], sl.Component[sl.DiscordTarget]],
) -> None:
    """Dispatch a usable authored key and check that the interaction is acknowledged.

    Choose from the controls that were registered for this render, skipping disabled
    actions and supplying one value for a picker. This calls message-root dispatch directly;
    it does not exercise Discord custom-ID decoding or assert a widget state change.
    """
    component = build()
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    commit_render(message_root)
    handlers = set(message_root.snapshot().handler_keys)

    key: str | None = None
    values: list[str] | None = None
    for node in engine.walk(engine.render_tree(component)):
        if not isinstance(node, KEYED_CONTROLS) or node.key not in handlers:
            continue
        if isinstance(node, ActionControl) and not node.available:
            continue
        key = node.key
        if isinstance(node, Choices):
            values = [node.choices[0].key]
        break

    assert key is not None, "every advertised widget draws at least one usable control"

    interaction = interaction_harness()
    # Values only when there are some: `dispatch(key, interaction, ())` is a *selection* of
    # nothing, and a button pressed that way never reaches its press handler.
    if values is None:
        await message_root.dispatch(key, interaction)
    else:
        await message_root.dispatch(key, interaction, values)

    assert interaction.response.is_done(), f"{key!r} reached no handler"
