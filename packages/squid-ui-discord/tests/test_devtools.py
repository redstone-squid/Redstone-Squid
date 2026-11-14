"""The generic, injected devtools cog over public runtime contracts."""

from datetime import UTC, datetime
from typing import Any, cast

import discord
import pytest
from discord.ext import commands

import squid_ui_discord
from squid_reactivity import EventLedger, OperationEventSnapshot, observe_events, transaction
from squid_ui_discord import Owner, live
from squid_ui_discord.devtools import DevTools, open_devtools
from squid_ui_discord.devtools_runtime import DevToolsRuntime
from squid_ui_discord.devtools_view import DevToolsSection
from squid_ui_discord.routing import Router
from squid_ui_discord.testing import (
    AsyncCallRecorder,
    ContextHarness,
    assert_within_limits,
    interaction_harness,
    message_harness,
)


class FakeBot:
    def __init__(self, *, owner: bool = True) -> None:
        self.is_owner = AsyncCallRecorder(result=owner)
        self.items: list[type[discord.ui.DynamicItem[Any]]] = []

    def add_dynamic_items(self, *items: type[discord.ui.DynamicItem[Any]]) -> None:
        self.items.extend(items)


class DevContext(ContextHarness):
    def __init__(self, *, bot: FakeBot | None = None) -> None:
        super().__init__(message=message_harness(), bot=FakeBot() if bot is None else bot, user_id=1)
        squid_ui_discord.install(cast(Any, self.bot))
        self.send_help = AsyncCallRecorder()


def make_context(*, bot: FakeBot | None = None) -> Any:
    return cast(Any, DevContext(bot=bot))


async def run(command: Any, cog: DevTools[Any], ctx: Any, *args: Any) -> None:
    """Invoke the command callback directly; owner and host checks are tested separately."""
    await command.callback(cog, ctx, *args)


class TestGate:
    async def test_owner_only_is_the_default(self) -> None:
        allowed = make_context(bot=FakeBot(owner=True))
        refused = make_context(bot=FakeBot(owner=False))
        cog = DevTools(allowed.bot)

        assert await cog.cog_check(allowed)
        assert not await cog.cog_check(refused)

    async def test_a_host_can_inject_its_policy(self) -> None:
        check = AsyncCallRecorder(result=True)
        ctx = make_context()

        assert await DevTools(ctx.bot, check).cog_check(ctx)
        check.assert_awaited_once_with(ctx)


class TestDashboardCommand:
    async def test_command_tree_has_one_optional_tab_choice(self) -> None:
        async with commands.Bot(command_prefix="!", intents=discord.Intents.none()) as bot:
            runtime = squid_ui_discord.install(bot)
            await bot.add_cog(DevTools(bot))

            command = bot.get_command("dev ui")
            assert isinstance(command, commands.HybridCommand)
            assert not isinstance(command, commands.Group)
            assert set(command.clean_params) == {"tab"}
            assert {item.qualified_name for item in bot.walk_commands() if item.root_parent is not None} == {"dev ui"}
            group = bot.tree.get_command("dev")
            assert isinstance(group, discord.app_commands.Group)
            payload = group.to_dict(bot.tree)
            assert payload["default_member_permissions"] == discord.Permissions(administrator=True).value
            (ui,) = payload["options"]
            assert ui["name"] == "ui"
            assert ui["type"] == discord.AppCommandOptionType.subcommand.value
            (tab,) = ui["options"]
            assert tab["name"] == "tab"
            assert not tab["required"]
            assert {choice["value"] for choice in tab["choices"]} == set(DevToolsSection)
            await runtime.close()

    async def test_host_command_can_open_the_shared_dashboard(self) -> None:
        ctx = make_context()

        message_root = await open_devtools(ctx, runtime=DevToolsRuntime())

        assert message_root.snapshot().access == Owner(1)
        assert message_root in live.message_roots()

    async def test_omitting_tab_opens_overview_owned_by_the_caller(self) -> None:
        ctx = make_context()
        cog = DevTools(ctx.bot)

        await run(cog.ui_command, cog, ctx)

        (root,) = live.message_roots()
        assert root.snapshot().access == Owner(1)
        assert "Operational dashboard" in str(ctx.send.await_args.kwargs["view"].to_components())

    async def test_slash_dashboard_is_ephemeral(self) -> None:
        ctx = make_context()
        ctx.interaction = interaction_harness()
        ctx.interaction.client = ctx.bot
        cog = DevTools(ctx.bot)

        await run(cog.ui_command, cog, ctx, "activity")

        (root,) = live.message_roots()
        assert root.snapshot().access == Owner(1)
        assert ctx.send.await_args.kwargs["ephemeral"]

    @pytest.mark.parametrize(
        ("tab", "heading"),
        [
            ("overview", "Operational dashboard"),
            ("roots", "Message roots"),
            ("sessions", "Live sessions"),
            ("routes", "Routed controls"),
            ("activity", "Action results"),
            ("queues", "Queues and subscribers"),
            ("profile", "Profiler"),
            ("persistence", "Persistence"),
        ],
    )
    async def test_tab_opens_the_selected_dashboard_section(self, tab: str, heading: str) -> None:
        ctx = make_context()
        cog = DevTools(ctx.bot)

        await run(cog.ui_command, cog, ctx, tab)

        view = ctx.send.await_args.kwargs["view"]
        assert heading in str(view.to_components())
        assert_within_limits(view)

    async def test_prefix_tab_choices_reject_unknown_tabs(self) -> None:
        ctx = make_context()
        cog = DevTools(ctx.bot)
        parameter = cog.ui_command.clean_params["tab"]

        assert await commands.run_converters(ctx, parameter.converter, "activity", parameter) == "activity"
        with pytest.raises(commands.BadLiteralArgument):
            await commands.run_converters(ctx, parameter.converter, "unknown", parameter)

    async def test_activity_uses_ledger_when_profiler_has_no_trace(self) -> None:
        ledger = EventLedger()
        with observe_events(ledger):
            with transaction():
                pass
            ctx = make_context()
            cog = DevTools(ctx.bot, action_ledger=ledger)

            await run(cog.ui_command, cog, ctx, "activity")

            rendered = str(ctx.send.await_args.kwargs["view"].to_components())
            assert "Action results" in rendered
            assert ledger.results[0].action_id in rendered

    async def test_activity_renders_operation_nodes_with_profiler_disabled(self) -> None:
        ledger = EventLedger()
        ledger.accept(OperationEventSnapshot("execution-1", None, None, "publish", "succeeded", datetime.now(UTC)))
        ctx = make_context()
        cog = DevTools(ctx.bot, action_ledger=ledger)

        await run(cog.ui_command, cog, ctx, "activity")

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert "execution-1" in rendered
        assert "publish succeeded" in rendered

    async def test_routes_uses_the_context_client(self) -> None:
        async with commands.Bot(command_prefix="!", intents=discord.Intents.none()) as bot:
            runtime = squid_ui_discord.install(bot)
            router = Router()

            async def close(_interaction: discord.Interaction) -> None: ...

            router.add("panel:close", close)
            router.register(bot)
            ctx = cast(Any, ContextHarness(message=message_harness(), bot=bot, user_id=1))
            cog = DevTools(bot)

            await run(cog.ui_command, cog, ctx, "routes")

            rendered = str(ctx.send.await_args.kwargs["view"].to_components())
            assert "panel:close" in rendered
            await runtime.close()
