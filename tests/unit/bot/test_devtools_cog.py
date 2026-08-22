"""The `!dev ui` commands: owner-gated, private, and honest about a missing id."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

import squid.bot.app
import squid_layouts as sl
from squid.bot.app import DEVELOPMENT_EXTENSIONS, EXTENSIONS
from squid.bot.devtools import DevTools
from squid.bot.routes import router
from squid_layouts.discord import Mount, live
from squid_layouts.discord.testing import delivered_to, fake_message
from squid_layouts.primitives import Heading


class Subject(sl.Component):
    def render(self):
        return [Heading("Subject")]


@pytest.fixture(autouse=True)
def _isolated_registry():
    live._LIVE.clear()
    yield
    live._LIVE.clear()


def make_context(*, owner: bool = True) -> Any:
    """A prefix-command context stub exposing only what the cog touches."""
    return SimpleNamespace(
        interaction=None,
        guild=SimpleNamespace(id=5, preferred_locale="en-US"),
        author=SimpleNamespace(id=1, send=AsyncMock(return_value=AsyncMock(spec=discord.Message))),
        send=AsyncMock(return_value=AsyncMock(spec=discord.Message)),
        bot=SimpleNamespace(is_owner=AsyncMock(return_value=owner)),
    )


def make_cog() -> DevTools[Any]:
    return DevTools(cast("squid.bot.app.RedstoneSquid", SimpleNamespace()))


async def run(command: Any, cog: DevTools[Any], ctx: Any, *args: Any) -> None:
    """Invoke a command's body directly.

    A `Command` bound on a cog that was never added to a bot has no `cog` to inject, so the
    callback is called explicitly rather than through the descriptor.
    """
    await command.callback(cog, ctx, *args)


def test_devtools_loads_only_in_development_mode() -> None:
    """A production process never loads it at all, owner check or no owner check."""
    assert "squid.bot.devtools" in DEVELOPMENT_EXTENSIONS
    assert "squid.bot.devtools" not in EXTENSIONS


class TestGate:
    async def test_only_the_owner_passes_the_cog_check(self) -> None:
        assert await make_cog().cog_check(make_context(owner=True))
        assert not await make_cog().cog_check(make_context(owner=False))


class TestScene:
    async def test_it_direct_messages_the_scene_as_a_file(self) -> None:
        subject = Mount(Subject())
        await subject.send(delivered_to(fake_message()))
        ctx = make_context()

        cog = make_cog()
        await run(cog.dump_scene, cog, ctx, subject.id)

        files = ctx.author.send.await_args.kwargs["files"]
        assert [file.filename for file in files] == [f"scene-{subject.id}-gen1.json"]
        assert sl.scene.Codec.loads(files[0].fp.read().decode()) == subject.snapshot().scene

    async def test_an_unknown_id_is_refused_rather_than_dumped(self) -> None:
        ctx = make_context()

        cog = make_cog()
        await run(cog.dump_scene, cog, ctx, "nope")

        assert "nope" in str(ctx.author.send.await_args.kwargs["view"].to_components())
        assert "files" not in ctx.author.send.await_args.kwargs

    async def test_a_mount_with_no_committed_render_is_refused(self) -> None:
        # Reachable only by racing an open: the registry lists a mount from its first commit,
        # so an uncommitted one has to be found by an id the caller already had.
        subject = Mount(Subject())
        live.track(subject)
        ctx = make_context()

        cog = make_cog()
        await run(cog.dump_scene, cog, ctx, subject.id)

        assert "no scene" in str(ctx.author.send.await_args.kwargs["view"].to_components())


class TestRoutes:
    async def test_route_table_is_delivered_privately(self) -> None:
        ctx = make_context()

        cog = make_cog()
        await run(cog.list_routes, cog, ctx)

        ctx.author.send.assert_awaited_once()
        rendered = str(ctx.author.send.await_args.kwargs["view"].to_components())
        route = router.describe()[0]
        assert route.format in rendered
        assert route.handler_qualname in rendered
        assert route.group_prefix is not None
        assert route.group_prefix in rendered
        assert route.middleware[0] in rendered


class TestOpen:
    async def test_listing_sends_a_private_inspector_locked_to_the_caller(self) -> None:
        ctx = make_context()

        cog = make_cog()
        await run(cog.list_mounts, cog, ctx)

        ctx.author.send.assert_awaited_once()
        # The panel is a live mount like any other, and says so in its own list.
        assert len(live.mounts()) == 1
        assert live.mounts()[0].lock_to == frozenset({1})
