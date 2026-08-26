"""A toggle is one boolean identity with explicit state ownership."""

from collections.abc import Awaitable, Callable

import squid_ui as sl
from squid_discord import DISCORD_V2_DPY27
from squid_ui import scene
from squid_ui.forms import FormLike, SubmitHandler
from squid_ui.interactions import ActionMode, Actor, PressEvent, Visibility
from squid_ui.primitives.styles import ActionStyle
from squid_ui.runtime import PresentationSession
from squid_ui.runtime.component import render_component_tree


class _Responder:
    def __init__(self) -> None:
        self.acknowledged = False
        self.invalidated = False

    async def acknowledge(self) -> None:
        self.acknowledged = True

    async def notice(self, text: sl.TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None: ...

    async def redirect(self, url: str) -> None: ...

    async def finish(self) -> None: ...

    async def present_form(
        self,
        form: FormLike,
        *,
        key: str = "form",
        on_submit: SubmitHandler | None = None,
        mode: ActionMode | None = None,
        label: sl.TextLike = "",
        record=None,
    ) -> None: ...

    def invalidate(self) -> None:
        self.invalidated = True


def _event(responder: _Responder | None = None) -> PressEvent:
    return PressEvent(Actor("7"), responder or _Responder())


def _button(result: sl.scene.PlanResult) -> scene.Button:
    row = next(node for node in result.scene.components_v2.children if isinstance(node, scene.Row))
    button = row.items[0]
    assert isinstance(button, scene.Button)
    return button


def _recorder[EventT]() -> tuple[list[EventT], Callable[[EventT], Awaitable[None]]]:
    seen: list[EventT] = []

    async def record(event: EventT) -> None:
        seen.append(event)

    return seen, record


def test_factory_builds_one_boolean_node() -> None:
    ownership = sl.managed(initial=True)

    assert sl.toggle("Notifications", key="notices", on=ownership, tone=sl.Tone.SUCCESS) == sl.semantic.Toggle(
        "notices", "Notifications", ownership, tone=sl.Tone.SUCCESS
    )


async def test_managed_toggle_flips_session_state_and_invalidates() -> None:
    session = PresentationSession()
    responder = _Responder()
    node = sl.toggle("Notifications", key="notices")

    initial = sl.planning.plan(node, target=DISCORD_V2_DPY27, session=session)
    assert _button(initial).label == "Notifications: Off"

    await initial.bindings["notices"].handler(_event(responder))

    assert session.toggle("notices").on
    assert responder.acknowledged
    assert responder.invalidated
    assert _button(sl.planning.plan(node, target=DISCORD_V2_DPY27, session=session)).label == "Notifications: On"


async def test_controlled_toggle_reports_flipped_value_without_writing_session() -> None:
    session = PresentationSession()
    session.set_toggle("notices", on=True)
    seen: list[sl.ToggleEvent]
    seen, record = _recorder()
    node = sl.toggle("Notifications", key="notices", on=sl.controlled(value=False, on_change=record))

    result = sl.planning.plan(node, target=DISCORD_V2_DPY27, session=session)
    await result.bindings["notices"].handler(_event())

    assert _button(result).label == "Notifications: Off"
    assert [event.value for event in seen] == [True]
    assert session.toggle("notices").on


def test_toggle_lowering_uses_one_toned_button_and_custom_labels() -> None:
    result = sl.planning.plan(
        sl.toggle(
            "Web",
            key="web",
            on=sl.managed(initial=True),
            on_label="Enabled",
            off_label="Disabled",
            tone=sl.Tone.DANGER,
            available=False,
        ),
        target=DISCORD_V2_DPY27,
    )

    button = _button(result)
    assert button.label == "Web: Enabled"
    assert button.style is ActionStyle.DANGER
    assert button.disabled


def test_toggle_key_is_prefixed_through_embed() -> None:
    class Child(sl.Component):
        def render(self):
            return sl.toggle("Web", key="web")

    class Parent(sl.Component):
        def __init__(self) -> None:
            self.child = Child()

        def render(self):
            return self.boundary(self.child, key="settings")

    tree = render_component_tree(Parent())

    assert isinstance(tree.nodes[0], sl.semantic.Toggle)
    assert tree.nodes[0].key == "settings.web"
