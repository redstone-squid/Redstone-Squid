"""Command decorators that inject a request and return discord.py's own objects."""

from typing import Any, Self, cast

import discord
import pytest
from discord import app_commands
from discord.ext import commands

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui import paragraph
from squid_ui.forms import FormSpec, TextField
from squid_ui_discord.delivery import DeliveryResult
from squid_ui_discord.testing import ContextHarness, InteractionHarness, payload_texts


def installed_bot(config: sd.DiscordUIConfig | None = None) -> commands.Bot:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cast(Any, bot).ui = sd.install(bot, config)
    return bot


def interaction_for(bot: commands.Bot, *, user_id: int = 7) -> InteractionHarness:
    return InteractionHarness(user_id=user_id, client=bot)


async def run(command: object, *args: object) -> None:
    """Call a command's callback the way discord.py does: binding first, then the source."""
    await cast(Any, command).callback(*args)


class Builds(sd.Cog[commands.Bot]):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.request: sd.Request[Self] | None = None

    @sd.command(defer="private")
    async def build(self, request: sd.Request[Self], build_id: int) -> str:
        self.request = request
        return f"Build {build_id}"

    @sd.hybrid_command()
    async def prefix(self, request: sd.Request[Self]) -> str:
        self.request = request
        return "Prefix"

    @sd.autocomplete()
    async def choices(self, request: sd.Request[Self], current: str) -> list[tuple[str, str]]:
        assert request.owner is self
        return [(f"{current} {index}", str(index)) for index in range(30)]


async def test_command_is_a_native_command_that_injects_the_cog_request() -> None:
    bot = installed_bot()
    cog = Builds(bot)
    interaction = interaction_for(bot)

    assert isinstance(cog.build, app_commands.Command)
    assert cog.build.binding is cog
    await run(cog.build, cog, interaction.source, 42)

    assert cog.request is not None
    assert cog.request.owner is cog
    assert cog.request.scope is cog.ui
    assert cog.request.source is interaction.source
    assert interaction.response.defer.await_args.kwargs == {"ephemeral": True, "thinking": True}
    interaction.edit_original_response.assert_awaited_once()


async def test_command_signature_shows_discord_the_native_source() -> None:
    parameters = Builds.build._params
    assert list(parameters) == ["build_id"]
    assert parameters["build_id"].type is discord.AppCommandOptionType.integer


async def test_the_request_is_memoized_on_the_interaction() -> None:
    bot = installed_bot()
    cog = Builds(bot)
    interaction = interaction_for(bot)

    await run(cog.build, cog, interaction.source, 1)

    assert await sd.request(interaction.source) is cog.request
    assert interaction.extras["squid_ui_discord.request"] is cog.request


async def test_hybrid_command_answers_a_prefix_context() -> None:
    bot = installed_bot()
    cog = Builds(bot)
    context = ContextHarness(bot=bot)

    assert isinstance(cog.prefix, commands.HybridCommand)
    await run(cog.prefix, cog, context.source)

    assert cog.request is not None
    assert cog.request.owner is cog
    context.send.assert_awaited_once()


async def test_autocomplete_normalizes_and_limits_choices() -> None:
    bot = installed_bot()
    cog = Builds(bot)
    interaction = interaction_for(bot)

    choices = await cog.choices(interaction.source, "redstone")

    assert len(choices) == 25
    assert choices[0] == app_commands.Choice(name="redstone 0", value="0")


class Forms(sd.Cog[commands.Bot]):
    @sd.command()
    async def open(self, request: sd.Request[Self]) -> FormSpec:
        return FormSpec("Edit", (TextField(key="name", label="Name"),))


async def test_a_returned_form_opens_as_the_initial_response() -> None:
    bot = installed_bot()
    cog = Forms(bot)
    interaction = interaction_for(bot)

    await run(cog.open, cog, interaction.source)

    assert len(interaction.modals) == 1
    assert not interaction.sends


class Grouped(sd.Cog[commands.Bot]):
    admin = sd.Group(name="admin", description="Administration", defer="public")

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.request: sd.Request[Self] | None = None

    @admin.command(name="inherits")
    async def inherits(self, request: sd.Request[Self]) -> str:
        self.request = request
        return "Inherited"

    @admin.command(name="overrides", defer="private")
    async def overrides(self, request: sd.Request[Self]) -> str:
        self.request = request
        return "Overridden"


async def test_instance_group_members_inherit_defer_and_bind_to_the_cog() -> None:
    bot = installed_bot()
    cog = Grouped(bot)
    interaction = interaction_for(bot)
    interaction.command = cog.inherits

    assert cog.inherits.parent is cog.admin
    assert cog.inherits.binding is cog
    await run(cog.inherits, cog, interaction.source)

    assert interaction.response.defer.await_args.kwargs == {"ephemeral": False, "thinking": True}
    assert cog.request is not None and cog.request.scope is cog.ui


async def test_a_member_policy_overrides_its_group() -> None:
    bot = installed_bot()
    cog = Grouped(bot)
    interaction = interaction_for(bot)
    interaction.command = cog.overrides

    await run(cog.overrides, cog, interaction.source)

    assert interaction.response.defer.await_args.kwargs == {"ephemeral": True, "thinking": True}


class Tools(sd.Group, name="tools", description="Tools"):
    defer = "private"

    @sd.command(name="ping")
    async def ping(self, request: sd.Request[Self]) -> str:
        return "Pong"


async def test_class_body_group_members_inherit_the_class_policy() -> None:
    bot = installed_bot()
    tools = Tools()
    interaction = interaction_for(bot)
    interaction.command = tools.ping

    assert tools.ping.binding is tools
    await run(tools.ping, tools, interaction.source)

    assert interaction.response.defer.await_args.kwargs == {"ephemeral": True, "thinking": True}
    assert (await sd.request(interaction.source)).scope is cast(Any, bot).ui.app


async def test_defer_then_resolving_the_request_again_shares_the_ledger() -> None:
    """The bug the old surface had: a helper re-resolving a deferred interaction lost the defer."""
    bot = installed_bot()
    cog = Builds(bot)
    interaction = interaction_for(bot)

    await run(cog.build, cog, interaction.source, 1)
    again = await sd.request(interaction.source)

    assert again.deferred == "private"
    assert again.responded


async def test_a_foreign_defer_is_rejected() -> None:
    bot = installed_bot()
    interaction = interaction_for(bot)
    await interaction.response.defer(thinking=True)

    with pytest.raises(RuntimeError, match="outside this request ledger"):
        await (await sd.request(interaction.source)).respond("late")


def render_failure(request: sd.Request[Any], error: Exception) -> sl.LayoutNode:
    return paragraph(f"failed: {error}")


class Slow(sd.Cog[commands.Bot]):
    @sd.command(pending="Working…")
    async def work(self, request: sd.Request[Self], outcome: str) -> sl.LayoutNode:
        if outcome == "fail":
            raise ValueError("boom")
        return paragraph("Done")


async def test_pending_shows_a_card_then_the_result() -> None:
    bot = installed_bot()
    cog = Slow(bot)
    interaction = interaction_for(bot)

    await run(cog.work, cog, interaction.source, "ok")

    [sent] = interaction.sends
    assert "Working…" in payload_texts(sent.kwargs["view"])
    assert "Done" in payload_texts(interaction.edits[-1].kwargs["view"])


async def test_pending_failure_uses_the_error_policy_and_reraises() -> None:
    observed: list[tuple[Exception, DeliveryResult | None]] = []

    async def observe(request: sd.Request[Any], error: Exception, delivery: DeliveryResult | None) -> None:
        observed.append((error, delivery))

    bot = installed_bot(sd.DiscordUIConfig(errors=sd.ErrorPolicy(render=render_failure, observe=observe)))
    cog = Slow(bot)
    interaction = interaction_for(bot)

    with pytest.raises(ValueError, match="boom"):
        await run(cog.work, cog, interaction.source, "fail")

    assert "failed: boom" in payload_texts(interaction.edits[-1].kwargs["view"])
    [(error, delivery)] = observed
    assert isinstance(error, ValueError)
    assert delivery is not None


class Menus(sd.Cog[commands.Bot]):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.target: object | None = None

    @sd.context_menu(name="Inspect build", defer="public")
    async def inspect(self, request: sd.Request[Self], target: discord.Message) -> str:
        assert request.owner is self
        self.target = target
        return "Inspected"


async def test_context_menu_is_registered_invoked_and_removed_with_cog() -> None:
    bot = installed_bot()
    cog = Menus(bot)
    interaction = interaction_for(bot)
    target = object()

    await bot.add_cog(cog)
    menu = bot.tree.get_command("Inspect build", type=discord.AppCommandType.message)
    assert isinstance(menu, app_commands.ContextMenu)
    await menu._invoke(interaction.source, target)

    assert cog.target is target
    assert interaction.response.defer.await_args.kwargs == {"ephemeral": False, "thinking": True}
    await bot.remove_cog(cog.qualified_name)
    assert cog.ui.closed
    assert bot.tree.get_command("Inspect build", type=discord.AppCommandType.message) is None


async def test_failed_load_rolls_back_menu_and_scope() -> None:
    class Broken(Menus):
        async def ui_load(self) -> None:
            raise RuntimeError("broken")

    bot = installed_bot()
    cog = Broken(bot)

    with pytest.raises(RuntimeError, match="broken"):
        await bot.add_cog(cog)

    assert cog.ui.closed
    assert bot.tree.get_command("Inspect build", type=discord.AppCommandType.message) is None


def test_native_lifecycle_hooks_cannot_bypass_scope_cleanup() -> None:
    with pytest.raises(TypeError, match="ui_load/ui_unload"):

        class Unsafe(sd.Cog[commands.Bot]):
            async def cog_unload(self) -> None:
                pass


def test_request_must_occupy_the_native_source_slot() -> None:
    with pytest.raises(TypeError, match="native source slot"):

        @cast(Any, sd.command())
        async def misplaced(owner: object, value: int, request: sd.Request[object]) -> None:
            pass
