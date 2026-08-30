"""The generic, injected devtools cog over public runtime contracts."""

import json
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

import squid_ui as sl
import squid_ui_discord
from squid_reactivity import ActionLedger, OperationEventSnapshot, add_action_result_sink, transaction
from squid_ui.primitives import Button, Heading, Row
from squid_ui.profiling import MemoryProfiler, OperationKind
from squid_ui_discord import Everyone, MessageRoot, Owner, live
from squid_ui_discord.devtools import DevTools
from squid_ui_discord.devtools_runtime import DevToolsRuntime
from squid_ui_discord.routing import Router
from squid_ui_discord.testing import ContextHarness, commit_render, delivered_to, interaction_harness, message_harness


class Subject(sl.Component[sl.ComponentsV2Target]):
    def render(self):
        return [Heading("Subject")]


class Clicker(sl.Component[sl.ComponentsV2Target]):
    count: int = sl.state(0)

    def render(self):
        return [Heading("Clicker"), Row((Button(label="Bump", on_click=self.bump, key="bump"),))]

    async def bump(self, event) -> None:
        self.count += 1


class FakeBot:
    def __init__(self, *, owner: bool = True) -> None:
        self.is_owner = AsyncMock(return_value=owner)
        self.items: list[type[discord.ui.DynamicItem[Any]]] = []

    def add_dynamic_items(self, *items: type[discord.ui.DynamicItem[Any]]) -> None:
        self.items.extend(items)


class DevContext(ContextHarness):
    def __init__(self, *, bot: FakeBot | None = None) -> None:
        super().__init__(message=message_harness(), bot=FakeBot() if bot is None else bot, user_id=1)
        self.send_help = AsyncMock()


def make_context(*, bot: FakeBot | None = None) -> Any:
    return cast(Any, DevContext(bot=bot))


async def run(command: Any, cog: DevTools[Any], ctx: Any, *args: Any) -> None:
    await command.callback(cog, ctx, *args)


class TestGate:
    async def test_owner_only_is_the_default(self) -> None:
        allowed = make_context(bot=FakeBot(owner=True))
        refused = make_context(bot=FakeBot(owner=False))
        cog = DevTools()

        assert await cog.cog_check(allowed)
        assert not await cog.cog_check(refused)

    async def test_a_host_can_inject_its_policy(self) -> None:
        check = AsyncMock(return_value=True)
        ctx = make_context()

        assert await DevTools(check).cog_check(ctx)
        check.assert_awaited_once_with(ctx)


class TestMountCommands:
    async def test_list_opens_an_inspector_owned_by_the_caller(self) -> None:
        ctx = make_context()
        cog = DevTools()

        await run(cog.list_roots, cog, ctx)

        assert len(live.message_roots()) == 1
        assert live.message_roots()[0].snapshot().access == Owner(1)

    @pytest.mark.parametrize(("command", "expected"), (("dump_plan", "logical"), ("dump_metrics", "cache:")))
    async def test_snapshot_reports_render_with_squid_ui(self, command: str, expected: str) -> None:
        subject = MessageRoot(Subject(), access=Everyone())
        await subject.send(delivered_to(message_harness()))
        ctx = make_context()
        cog = DevTools()

        await run(getattr(cog, command), cog, ctx, subject.id)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert expected in rendered

    async def test_scene_is_attached_as_protocol_json(self) -> None:
        subject = MessageRoot(Subject(), access=Everyone())
        await subject.send(delivered_to(message_harness()))
        ctx = make_context()
        cog = DevTools()

        await run(cog.dump_scene, cog, ctx, subject.id)

        file = ctx.send.await_args.kwargs["files"][0]
        assert file.filename == f"scene-{subject.id}-gen1.json"
        assert sl.scene.Codec.loads(file.fp.read().decode()) == subject.snapshot().scene

    async def test_an_unknown_message_root_is_explained(self) -> None:
        ctx = make_context()
        cog = DevTools()

        await run(cog.dump_metrics, cog, ctx, "missing")

        assert "missing" in str(ctx.send.await_args.kwargs["view"].to_components())

    async def test_actions_uses_ledger_when_profiler_has_no_trace(self) -> None:
        ledger = ActionLedger()
        add_action_result_sink(ledger)
        try:
            with transaction():
                pass
            ctx = make_context()
            cog = DevTools(action_ledger=ledger)

            await run(cog.inspect_actions, cog, ctx)

            rendered = str(ctx.send.await_args.kwargs["view"].to_components())
            assert "Action results" in rendered
            assert ledger.results[0].action_id in rendered
        finally:
            ledger.close()

    async def test_actions_renders_operation_nodes_with_profiler_disabled(self) -> None:
        ledger = ActionLedger()
        ledger.accept(OperationEventSnapshot("execution-1", None, None, "publish", "succeeded", datetime.now(UTC)))
        ctx = make_context()
        cog = DevTools(action_ledger=ledger)

        await run(cog.inspect_actions, cog, ctx)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert "operation:execution-1" in rendered
        assert "publish succeeded" in rendered


class TestRoutes:
    async def test_every_router_on_the_context_client_is_rendered(self) -> None:
        bot = FakeBot()
        router = Router()

        async def close(_interaction) -> None: ...

        router.add("panel:close", close)
        router.register(bot)  # type: ignore[arg-type]
        ctx = make_context(bot=bot)
        cog = DevTools()

        await run(cog.list_routes, cog, ctx)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert "panel:close" in rendered
        assert "close" in rendered


class TestProfiles:
    async def test_profile_health_is_explicit(self) -> None:
        ctx = make_context()
        cog = DevTools(profiler=MemoryProfiler())

        await run(cog.inspect_profile, cog, ctx)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert "Profiler" in rendered

    async def test_action_aggregates_suppress_percentiles_below_sample_floor(self) -> None:
        profiler = MemoryProfiler()
        with profiler.operation(OperationKind.DISPATCH, name="save"):
            pass
        ctx = make_context()
        cog = DevTools(profiler=profiler)

        await run(cog.inspect_profile, cog, ctx)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert "save" in rendered
        assert "n=1" in rendered
        assert "p50" not in rendered

    async def test_message_root_profile_filters_bounded_traces_by_non_aggregate_attribute(self) -> None:
        profiler = MemoryProfiler()
        subject = MessageRoot(Subject(), access=Everyone(), profiler=profiler)
        await subject.send(delivered_to(message_harness()))
        assert {aggregate.key.counter_name for aggregate in profiler.snapshot().counter_aggregates} >= {
            "planner.calls",
            "planner.states_explored",
        }
        ctx = make_context()
        cog = DevTools(profiler=profiler)

        await run(cog.inspect_profile, cog, ctx, subject.id)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert f"Profile for message root {subject.id}" in rendered
        assert "send" in rendered
        assert "planner" in rendered

    async def test_timeline_reads_as_a_transcript_across_roots(self) -> None:
        profiler = MemoryProfiler()
        first = MessageRoot(Clicker(), access=Everyone(), profiler=profiler, timeout=None)
        second = MessageRoot(Clicker(), access=Everyone(), profiler=profiler, timeout=None)
        commit_render(first)
        commit_render(second)
        await first.dispatch("bump", interaction_harness(user_id=11))
        await second.dispatch("bump", interaction_harness(user_id=22))
        ctx = make_context()
        cog = DevTools(profiler=profiler)

        await run(cog.inspect_timeline, cog, ctx)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert "Dispatch timeline" in rendered
        assert "actor=11" in rendered
        assert "actor=22" in rendered
        # Oldest first: the second press must not be reported before the first.
        assert rendered.index("actor=11") < rendered.index("actor=22")
        assert "completed" in rendered

    async def test_timeline_filters_to_one_actor(self) -> None:
        profiler = MemoryProfiler()
        message_root = MessageRoot(Clicker(), access=Everyone(), profiler=profiler, timeout=None)
        commit_render(message_root)
        await message_root.dispatch("bump", interaction_harness(user_id=11))
        await message_root.dispatch("bump", interaction_harness(user_id=22))
        ctx = make_context()
        cog = DevTools(profiler=profiler)

        await run(cog.inspect_timeline, cog, ctx, 20, "actor:11")

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert "actor=11" in rendered
        assert "actor=22" not in rendered

    async def test_timeline_refuses_an_unreadable_filter(self) -> None:
        ctx = make_context()
        cog = DevTools(profiler=MemoryProfiler())

        await run(cog.inspect_timeline, cog, ctx, 20, "user=11")

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert "refused" in rendered

    async def test_queue_command_infers_bus_and_profiler_from_scheduler(self) -> None:
        profiler = MemoryProfiler()
        bus = sl.runtime.LocalTopicBus()
        scheduler = squid_ui_discord.MessageRootScheduler(bus, profiler=profiler)

        def refresh(topic) -> None:
            pass

        bus.subscribe(sl.runtime.Topic("build", "devtools"), refresh)
        bus.publish(sl.runtime.Topic("build", "devtools"))
        ctx = make_context()
        cog = DevTools(scheduler=scheduler)

        await run(cog.inspect_queues, cog, ctx)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert "scheduler" in rendered
        assert "topics" in rendered
        # Neither was passed, so "unconfigured" anywhere means an inference did not happen.
        assert "unconfigured" not in rendered
        assert "build:devtools subscribers=1" in rendered
        # A local bus publishes synchronously, so the press is delivered rather than queued.
        assert "delivered=1" in rendered
        assert DevToolsRuntime(scheduler=scheduler).profiler is profiler

    async def test_profile_export_attaches_round_trippable_snapshot(self) -> None:
        profiler = MemoryProfiler()
        with profiler.operation(OperationKind.SEND, name="panel"):
            pass
        ctx = make_context()
        cog = DevTools(profiler=profiler)

        await run(cog.export_ui, cog, ctx)

        file = ctx.send.await_args.kwargs["files"][0]
        payload = json.loads(file.fp.read().decode())
        assert file.filename == "squid-operational-snapshot.json"
        assert payload["profiler"]["schema_version"] == 1
        assert payload["profiler"]["aggregates"][0]["key"]["name"] == "panel"
