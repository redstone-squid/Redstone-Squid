"""The generic, injected devtools cog over public runtime contracts."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import Mount, Router, live
from squid_layouts.discord.devtools import DevTools
from squid_layouts.discord.testing import delivered_to, fake_message
from squid_layouts.primitives import Heading


class Subject(sl.Component):
    def render(self):
        return [Heading("Subject")]


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
    async def test_list_opens_an_inspector_locked_to_the_caller(self) -> None:
        ctx = make_context()
        cog = DevTools()

        await run(cog.list_mounts, cog, ctx)

        assert len(live.mounts()) == 1
        assert live.mounts()[0].lock_to == frozenset({1})

    @pytest.mark.parametrize(("command", "expected"), (("dump_plan", "logical"), ("dump_metrics", "cache:")))
    async def test_snapshot_reports_render_with_squid_layouts(self, command: str, expected: str) -> None:
        subject = Mount(Subject())
        await subject.send(delivered_to(fake_message()))
        ctx = make_context()
        cog = DevTools()

        await run(getattr(cog, command), cog, ctx, subject.id)

        rendered = str(ctx.send.await_args.kwargs["view"].to_components())
        assert expected in rendered

    async def test_scene_is_attached_as_protocol_json(self) -> None:
        subject = Mount(Subject())
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
