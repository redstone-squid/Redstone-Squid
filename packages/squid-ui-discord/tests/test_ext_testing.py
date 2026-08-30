"""Facade-level staging and command invocation helpers."""

from typing import Self, cast

import discord
from discord import app_commands
from discord.ext import commands

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_discord.ext as sdx
from squid_ui_discord.testing import ContextHarness, InteractionHarness


class Editor(sd.Screen[object]):
    audience = "personal"
    session = sd.SessionSpec("editor", scope=sd.ScopeKind.USER_GUILD)
    count: int = sl.state(0)
    name: str = sl.state("Old")

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

    async with sdx.testing.stage(screen, owner=owner, user_id=7) as ui:
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


class Commands(sdx.Cog[commands.Bot]):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.request: sdx.DiscordRequest[Self] | None = None

    @app_commands.command()
    @sdx.command(acknowledgement="private")
    async def inspect(self, request: sdx.DiscordRequest[Self], build_id: int) -> str:
        self.request = request
        return f"Build {build_id}"

    @commands.command()
    @sdx.command()
    async def prefix(self, request: sdx.DiscordRequest[Self]) -> str:
        self.request = request
        return "Prefix"


async def test_invoke_uses_the_real_slash_and_prefix_wrappers() -> None:
    cog = Commands(_installed_bot())

    slash = await sdx.testing.invoke(type(cog).inspect, 42, owner=cog, user_id=9)
    assert isinstance(slash, InteractionHarness)
    assert cog.request is not None and cog.request.user.id == 9
    slash.edit_original_response.assert_awaited_once()

    prefix = await sdx.testing.invoke(type(cog).prefix, owner=cog, user_id=10, source="context")
    assert isinstance(prefix, ContextHarness)
    assert cog.request is not None and cog.request.user.id == 10
    prefix.send.assert_awaited_once()
