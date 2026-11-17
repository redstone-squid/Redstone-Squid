"""Facade-level staging and command invocation helpers."""

from typing import Self, cast, override

import discord
import pytest
from discord.ext import commands

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.ext.discord.testing import ContextHarness, InteractionHarness


class Editor(sd.Screen[None, object]):
    policy = sd.ResponseSpec(audience="personal", session=sd.SessionSpec("editor", scope=sd.ScopeKind.USER_GUILD))
    count: int = sl.state(0)
    name: str = sl.state("Old")

    @override
    def render(self) -> sl.LayoutNode:
        form = sl.forms.FormSpec("Rename", (sl.forms.TextField(key="name", label="Name"),))
        return sl.stack(
            sl.paragraph(f"{self.name}: {self.count}"),
            sl.action_controls(sl.action_control("Add", self.add, key="editor.add"), key="editor.actions"),
            sl.choices(
                sl.choice("Redstone", key="redstone"),
                sl.choice("Slime", key="slime"),
                sl.choice("Piston", key="piston"),
                sl.choice("Observer", key="observer"),
                sl.choice("Comparator", key="comparator"),
                sl.choice("Repeater", key="repeater"),
                selection=sl.controlled((), self.filter),
                key="editor.filter",
            ),
            sl.form("Rename", form, key="editor.rename", on_submit=self.rename),
        )

    async def add(self, _event: sl.PressEvent) -> None:
        self.count += 1

    async def filter(self, event: sl.ChoiceEvent) -> None:
        self.count += len(event.selected)

    async def rename(self, event: sl.SubmitEvent) -> None:
        self.name = cast(str, event.values["name"])


async def test_stage_drives_facade_dispatch_and_forms_by_semantic_key() -> None:
    owner = object()
    screen = Editor()

    async with sd.testing.stage(screen, owner=owner, user_id=7) as ui:
        assert ui.result.component is screen
        assert ui.session is not None
        assert ui.root.access == sd.Owner(7)
        assert ui.texts() == ["Old: 0"]
        assert ui.control("editor.add").label == "Add"

        denied = await ui.press("editor.add", user_id=8)
        assert denied.response.send_message.await_count == 1
        assert screen.count == 0

        await ui.press("editor.add")
        await ui.select("editor.filter", ["redstone"])
        form = await ui.press_for_form("editor.rename")
        form.assert_within_limits()
        await form.submit({"name": "New name"})

        assert ui.texts() == ["New name: 2"]
        ui.assert_within_limits()

    assert ui.root.finished


def _installed_bot() -> commands.Bot:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.ui = sd.install(bot)  # type: ignore[attr-defined]
    return bot


class Commands(sd.Cog[commands.Bot]):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.request: sd.Request[Self] | None = None

    @sd.command(defer="private")
    async def inspect(self, request: sd.Request[Self], build_id: int) -> str:
        self.request = request
        return f"Build {build_id}"

    @sd.hybrid_command()
    async def prefix(self, request: sd.Request[Self]) -> str:
        self.request = request
        return "Prefix"


async def test_invoke_uses_the_real_slash_and_prefix_wrappers() -> None:
    cog = Commands(_installed_bot())

    slash = await sd.testing.invoke(type(cog).inspect, 42, owner=cog, user_id=9)
    assert isinstance(slash, InteractionHarness)
    assert cog.request is not None and cog.request.user.id == 9
    slash.edit_original_response.assert_awaited_once()

    prefix = await sd.testing.invoke(type(cog).prefix, owner=cog, user_id=10, source="context")
    assert isinstance(prefix, ContextHarness)
    assert cog.request is not None and cog.request.user.id == 10
    prefix.send.assert_awaited_once()


async def test_local_control_lookup_requires_a_unique_suffix() -> None:
    class Parent(sd.Screen):
        def __init__(self) -> None:
            self.first = Editor()
            self.second = Editor()

        @override
        def render(self) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
            return sl.stack(self.first, self.second)

    async with sd.testing.stage(Editor(), owner=object()) as ui:
        assert ui.key_for("add") == "editor.add"
        assert ui.key_for("editor.add") == "editor.add"
        with pytest.raises(KeyError, match="found "):
            ui.key_for("missing")
        with pytest.raises(KeyError, match="found "):
            ui.key_for("ditor.add")

    async with sd.testing.stage(Parent(), owner=object()) as ui:
        with pytest.raises(KeyError, match="expected one control"):
            ui.key_for("editor.add")


async def test_long_semantic_keys_survive_native_id_shortening() -> None:
    namespace = "nested-component-" * 5

    class Parent(sd.Screen):
        def __init__(self) -> None:
            self.editor = Editor()

        @override
        def render(self) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
            return self.editor.keyed(namespace)

    parent = Parent()
    async with sd.testing.stage(parent, owner=object()) as ui:
        key = ui.key_for("editor.add")
        assert key == f"{namespace}.editor.add"
        assert len(ui.control(key).payload["custom_id"]) <= 100
        await ui.press(key)
        assert parent.editor.count == 1


class SubmissionModal(discord.ui.Modal):
    def __init__(self) -> None:
        super().__init__(title="Edit", custom_id="modal-root")
        self.submissions = 0

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.submissions += 1


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("text", 42, "42"),
        ("radio", ["one"], "one"),
        ("radio", [], None),
        ("checkbox", True, True),
        ("select", [1], ["1"]),
        ("checkboxes", ["one"], ["one"]),
    ],
)
async def test_staged_form_populates_labelled_inputs_once(kind: str, value: object, expected: object) -> None:
    match kind:
        case "text":
            item = discord.ui.TextInput(custom_id="field")
        case "radio":
            item = discord.ui.RadioGroup(
                custom_id="field", options=[discord.RadioGroupOption(label="One", value="one")]
            )
        case "checkbox":
            item = discord.ui.Checkbox(custom_id="field")
        case "select":
            item = discord.ui.Select(custom_id="field", options=[discord.SelectOption(label="One", value="1")])
        case _:
            item = discord.ui.CheckboxGroup(
                custom_id="field", options=[discord.CheckboxGroupOption(label="One", value="one")]
            )
    modal = SubmissionModal()
    modal.add_item(discord.ui.Label(text="Field", component=item))
    async with discord.Client(intents=discord.Intents.none()) as client:
        form = sd.testing.StagedForm(modal, client=client, user_id=7)
        await form.submit({"field": value})
        actual = item.values if isinstance(item, discord.ui.Select | discord.ui.CheckboxGroup) else item.value
        assert actual == expected
        assert modal.submissions == 1
        with pytest.raises(RuntimeError, match="already submitted"):
            await form.submit({"field": value})


async def test_staged_form_rejects_modal_and_unknown_ids_and_keeps_first_duplicate() -> None:
    modal = SubmissionModal()
    first = discord.ui.TextInput(custom_id="field")
    second = discord.ui.TextInput(custom_id="field")
    modal.add_item(discord.ui.Label(text="First", component=first))
    modal.add_item(discord.ui.Label(text="Second", component=second))
    async with discord.Client(intents=discord.Intents.none()) as client:
        form = sd.testing.StagedForm(modal, client=client, user_id=7)
        for unknown in ("modal-root", "missing"):
            with pytest.raises(KeyError, match="no fields"):
                await form.submit({unknown: "ignored"})
        assert modal.submissions == 0
        await form.submit({"field": "first only"})
        assert first.value == "first only"
        assert second.value == ""
        assert modal.submissions == 1
