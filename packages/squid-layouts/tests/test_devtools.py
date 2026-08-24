"""The generic, injected devtools cog over public runtime contracts."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import Everyone, Mount, Owner, live
from squid_layouts.discord.devtools import DevTools
from squid_layouts.discord.operations import DevToolsRuntime
from squid_layouts.discord.routing import Router
from squid_layouts.discord.testing import commit_render, delivered_to, fake_interaction, fake_message
from squid_layouts.primitives import Button, Heading, Row
from squid_layouts.profiling import MemoryProfiler, OperationKind


class Subject(sl.Component):
    def render(self):
        return [Heading("Subject")]


class Clicker(sl.Component):
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


@pytest.fixture(autouse=True)
def _isolated_registry():
    live._LIVE.clear()
    yield
    live._LIVE.clear()


def make_context(*, bot: FakeBot | None = None) -> Any:
    return SimpleNamespace(
        interaction=None,
        author=SimpleNamespace(id=1),
        send=AsyncMock(return_value=fake_message()),
        send_help=AsyncMock(),
        bot=FakeBot() if bot is None else bot,
    )


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

        await run(cog.list_mounts, cog, ctx)

        assert len(live.mounts()) == 1
        assert live.mounts()[0].snapshot().access == Owner(1)

    @pytest.mark.parametrize(("command", "expected"), (("dump_plan", "logical"), ("dump_metrics", "cache:")))
    async def test_snapshot_reports_render_with_squid_layouts(self, command: str, expected: str) -> None:
        subject = Mount(Subject(), access=Everyone())
        await subject.send(delivered_to(fake_message()))
        ctx = make_context()
        cog = DevTools()

        await run(getattr(cog, command), cog, ctx, subject.id)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert expected in rendered

    async def test_scene_is_attached_as_protocol_json(self) -> None:
        subject = Mount(Subject(), access=Everyone())
        await subject.send(delivered_to(fake_message()))
        ctx = make_context()
        cog = DevTools()

        await run(cog.dump_scene, cog, ctx, subject.id)

        file = ctx.send.await_args.kwargs["files"][0]
        assert file.filename == f"scene-{subject.id}-gen1.json"
        assert sl.scene.Codec.loads(file.fp.read().decode()) == subject.snapshot().scene

    async def test_an_unknown_mount_is_explained(self) -> None:
        ctx = make_context()
        cog = DevTools()

        await run(cog.dump_metrics, cog, ctx, "missing")

        assert "missing" in str(ctx.send.await_args.kwargs["view"].to_components())


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

    async def test_mount_profile_filters_bounded_traces_by_non_aggregate_attribute(self) -> None:
        profiler = MemoryProfiler()
        subject = Mount(Subject(), access=Everyone(), profiler=profiler)
        await subject.send(delivered_to(fake_message()))
        assert {aggregate.key.counter_name for aggregate in profiler.snapshot().counter_aggregates} >= {
            "planner.calls",
            "planner.states_explored",
        }
        ctx = make_context()
        cog = DevTools(profiler=profiler)

        await run(cog.inspect_profile, cog, ctx, subject.id)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert f"Profile for mount {subject.id}" in rendered
        assert "send" in rendered
        assert "planner" in rendered

    async def test_timeline_reads_as_a_transcript_across_mounts(self) -> None:
        profiler = MemoryProfiler()
        first = Mount(Clicker(), access=Everyone(), profiler=profiler, timeout=None)
        second = Mount(Clicker(), access=Everyone(), profiler=profiler, timeout=None)
        commit_render(first)
        commit_render(second)
        await first.dispatch("bump", fake_interaction(user_id=11))
        await second.dispatch("bump", fake_interaction(user_id=22))
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
        mount = Mount(Clicker(), access=Everyone(), profiler=profiler, timeout=None)
        commit_render(mount)
        await mount.dispatch("bump", fake_interaction(user_id=11))
        await mount.dispatch("bump", fake_interaction(user_id=22))
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

    async def test_queue_command_infers_bus_and_profiler_from_reactor(self) -> None:
        profiler = MemoryProfiler()
        bus = sl.runtime.LocalTopicBus()
        reactor = sl.discord.Reactor(bus, profiler=profiler)

        def refresh(topic) -> None:
            pass

        bus.subscribe(sl.runtime.Topic("build", "devtools"), refresh)
        bus.publish(sl.runtime.Topic("build", "devtools"))
        ctx = make_context()
        cog = DevTools(reactor=reactor)

        await run(cog.inspect_queues, cog, ctx)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert "reactor" in rendered
        assert "topics" in rendered
        # Neither was passed, so "unconfigured" anywhere means an inference did not happen.
        assert "unconfigured" not in rendered
        assert "build:devtools subscribers=1" in rendered
        # A local bus publishes synchronously, so the press is delivered rather than queued.
        assert "delivered=1" in rendered
        assert DevToolsRuntime(reactor=reactor).profiler is profiler

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
        assert payload["profiler"]["schema_version"] == 2
        assert payload["profiler"]["aggregates"][0]["key"]["name"] == "panel"
