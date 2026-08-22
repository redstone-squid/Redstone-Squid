"""A toggle is one boolean identity with explicit state ownership."""

from collections.abc import Awaitable, Callable

import squid_layouts as sl
from squid_layouts.actions import ActionPolicy, Actor, PressEvent, Visibility
from squid_layouts.discord import DEFAULT_TARGET
from squid_layouts.forms import FormLike, SubmitHandler
from squid_layouts.primitives.styles import ActionStyle
from squid_layouts.runtime import PresentationSession
from squid_layouts.runtime.component import render_component_tree
from squid_layouts.scene.model import SceneButton, SceneRow


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
        policy: ActionPolicy | None = None,
    ) -> None: ...

    def invalidate(self) -> None:
        self.invalidated = True


def _event(responder: _Responder | None = None) -> PressEvent:
    return PressEvent(Actor("7"), responder or _Responder())


def _button(result: sl.PlanResult) -> SceneButton:
    row = next(node for node in result.scene.children if isinstance(node, SceneRow))
    button = row.items[0]
    assert isinstance(button, SceneButton)
    return button


def _recorder[EventT]() -> tuple[list[EventT], Callable[[EventT], Awaitable[None]]]:
    seen: list[EventT] = []

    async def record(event: EventT) -> None:
        seen.append(event)

    return seen, record


def test_factory_builds_one_boolean_node() -> None:
    ownership = sl.managed(initial=True)

    assert sl.toggle("Notifications", key="notices", on=ownership, tone=sl.Tone.SUCCESS) == sl.Toggle(
        "notices", "Notifications", ownership, tone=sl.Tone.SUCCESS
    )


async def test_managed_toggle_flips_session_state_and_invalidates() -> None:
    session = PresentationSession()
    responder = _Responder()
    node = sl.toggle("Notifications", key="notices")

    initial = sl.plan(node, target=DEFAULT_TARGET, session=session)
    assert _button(initial).label == "Notifications: Off"

    await initial.bindings["notices"].handler(_event(responder))

    assert session.toggle("notices").on
    assert responder.acknowledged
    assert responder.invalidated
    assert _button(sl.plan(node, target=DEFAULT_TARGET, session=session)).label == "Notifications: On"


async def test_controlled_toggle_reports_flipped_value_without_writing_session() -> None:
    session = PresentationSession()
    session.set_toggle("notices", on=True)
    seen: list[sl.ToggleEvent]
    seen, record = _recorder()
    node = sl.toggle("Notifications", key="notices", on=sl.controlled(value=False, on_change=record))

    result = sl.plan(node, target=DEFAULT_TARGET, session=session)
    await result.bindings["notices"].handler(_event())

    assert _button(result).label == "Notifications: Off"
    assert [event.value for event in seen] == [True]
    assert session.toggle("notices").on


def test_toggle_lowering_uses_one_toned_button_and_custom_labels() -> None:
    result = sl.plan(
        sl.toggle(
            "Web",
            key="web",
            on=sl.managed(initial=True),
            on_label="Enabled",
            off_label="Disabled",
            tone=sl.Tone.DANGER,
            available=False,
        ),
        target=DEFAULT_TARGET,
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

    assert isinstance(tree.nodes[0], sl.Toggle)
    assert tree.nodes[0].key == "settings.web"
