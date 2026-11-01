"""Invocation contexts survive Discord dispatch and semantic event adaptation."""

from typing import override

import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui.handlers import FrontendHandler
from squid_ui.html.target import target as html_target
from squid_ui.planning import plan


class ContextScreen(sd.Screen):
    seen: list[sd.Request]

    def __init__(self) -> None:
        self.seen = []

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return sl.stack(
            sl.action_controls(sl.action_control("Press", self.press, key="press"), key="actions"),
            sl.choices(
                *(sl.choice(str(i), key=str(i)) for i in range(6)),
                selection=sl.controlled((), self.choose),
                key="choose",
            ),
            sl.form(
                "Submit",
                sl.forms.FormSpec("Name", (sl.forms.TextField(key="name", label="Name"),)),
                key="form",
                on_submit=self.submit,
            ),
        )

    async def press(self, event: sd.PressEvent) -> None:
        self.seen.append(event.context)
        assert event.context is await sd.request(event)
        await event.context.respond("Hello", audience="personal")

    async def choose(self, event: sd.ChoiceEvent) -> None:
        assert event.selected == ("1",)
        self.seen.append(event.context)

    async def submit(self, event: sd.SubmitEvent) -> None:
        assert event.values["name"] == "Ada"
        self.seen.append(event.context)


async def test_context_is_owned_memoized_and_preserved_by_choices_and_forms() -> None:
    owner = object()
    screen = ContextScreen()
    async with sd.testing.stage(screen, owner=owner) as ui:
        await ui.press("press")
        await ui.select("choose", ["1"])
        form = await ui.press_for_form("form")
        await form.submit({"name": "Ada"})
        assert len(screen.seen) == 3
        assert len({id(request) for request in screen.seen}) == 3
        assert all(request.owner is owner and request.root is ui.root for request in screen.seen)


async def test_frontend_branch_and_portable_fallback_are_selected_before_dispatch() -> None:
    seen: list[str] = []

    async def discord(event: sd.PressEvent) -> None:
        seen.append("discord")
        assert isinstance(event.context, sd.Request)

    async def fallback(event: sl.PressEvent) -> None:
        seen.append("fallback")

    callback = sl.handlers(sd.on_press(discord), fallback=fallback)
    node = sl.action_controls(sl.action_control("Press", callback, key="press"), key="actions")
    html = plan(node, target=html_target())
    assert html.bindings["press"].handler is fallback

    class Panel(sd.Screen):
        @override
        def render(self) -> sl.LayoutNode:
            return node

    async with sd.testing.stage(Panel(), owner=object()) as ui:
        await ui.press("press")
    assert seen == ["discord"]


def test_missing_frontend_and_duplicates_fail_before_exposure() -> None:
    async def discord(event: sd.PressEvent) -> None:
        pass

    branch = sd.on_press(discord)
    with pytest.raises(ValueError, match="repeat"):
        sl.handlers(branch, branch)
    with pytest.raises(TypeError, match="cannot be bound"):
        plan(sl.action_controls(sl.action_control("Press", branch, key="press"), key="actions"), target=html_target())


def test_generated_grouped_action_bindings_validate_frontend_coverage() -> None:
    async def portable(event: sl.PressEvent) -> None:
        pass

    wrong = FrontendHandler("html", portable)
    node = sl.action_controls(
        *(sl.action_control(str(i), wrong, key=str(i)) for i in range(8)),
        key="group",
        display=sl.semantic.ControlDisplay.GROUPED,
    )
    with pytest.raises(TypeError, match="cannot be bound"):
        plan(node, target=sd.DISCORD_V2_DPY27)


def test_missing_handler_set_branch_and_wrong_component_frontend_fail_at_mount() -> None:
    async def discord(event: sd.PressEvent) -> None:
        pass

    node = sl.action_controls(sl.action_control("Press", sl.handlers(sd.on_press(discord)), key="press"), key="actions")
    with pytest.raises(TypeError, match="no handler implementation"):
        plan(node, target=html_target())

    class Foreign(sl.Component[sl.ComponentsV2Target]):
        event_frontend = "html"

        @override
        def render(self) -> sl.LayoutNode:
            return sl.paragraph("Wrong frontend")

    root = sd.MessageRoot(Foreign(), access=sd.Everyone(), timeout=None)
    with pytest.raises(TypeError, match="requires frontend"):
        sd.testing.commit_render(root)


async def test_invalid_form_keeps_context_and_retry_authority() -> None:
    seen: list[sd.Request] = []

    async def invalid(event: sd.InvalidSubmitEvent) -> None:
        assert event.errors
        seen.append(event.context)
        await event.retry()

    async def valid(event: sd.SubmitEvent) -> None:
        seen.append(event.context)

    class FormPanel(sd.Screen):
        @override
        def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
            return sl.form(
                "Submit",
                sl.forms.FormSpec(
                    "Number",
                    (sl.forms.IntField(key="number", label="Number"),),
                    on_invalid=invalid,
                ),
                key="number",
                on_submit=valid,
            )

    owner = object()
    async with sd.testing.stage(FormPanel(), owner=owner) as ui:
        form = await ui.press_for_form("number")
        await form.submit({"number": "invalid"})
        assert len(seen) == 1
        assert seen[0].owner is owner
