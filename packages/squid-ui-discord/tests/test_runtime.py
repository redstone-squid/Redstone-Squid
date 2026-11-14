"""Installing the Discord runtime on a client, and finding it again from a click."""

import asyncio
from typing import Any, cast, override

import anyio
import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_reactivity import LocalTopicBus
from squid_reactivity.topics import Topic
from squid_ui.primitives import Heading
from squid_ui_discord import DiscordUIRuntime, install
from squid_ui_discord.runtime import _INSTALLED, DiscordUIRuntimeMissing
from squid_ui_discord.testing import (
    AsyncCallRecorder,
    ContextHarness,
    delivered_to,
    interaction_harness,
    message_harness,
)


class Panel(sl.Component[sl.ComponentsV2Target]):
    @override
    def render(self):
        return [Heading("Panel")]


class FakeClient:
    """A client stand-in: the weak table only needs a weak-referenceable key."""


def fake_client() -> Any:
    return FakeClient()


def test_a_second_install_on_one_client_is_refused() -> None:
    """One client must identify one UI runtime when an interaction looks it up."""
    client = fake_client()
    install(cast(discord.Client, client))

    with pytest.raises(ValueError, match="already has a layout host"):
        install(cast(discord.Client, client))


def test_of_resolves_from_a_client_an_interaction_and_a_command_context() -> None:
    client = fake_client()
    runtime = install(cast(discord.Client, client))
    interaction = interaction_harness(user_id=7)
    interaction.client = client
    context = ContextHarness(bot=client, user_id=7)

    assert DiscordUIRuntime.of(cast(Any, client)) is runtime
    assert DiscordUIRuntime.of(interaction) is runtime
    assert DiscordUIRuntime.of(context.source) is runtime


def test_of_raises_rather_than_returning_none_when_nothing_is_installed() -> None:
    """A missing installation is a wiring bug, so every caller would grow the same raise."""
    with pytest.raises(DiscordUIRuntimeMissing, match="no layout host is installed"):
        DiscordUIRuntime.of(cast(Any, fake_client()))


def test_of_survives_a_source_that_cannot_be_a_weak_key() -> None:
    """An unhashable source was never a key here; it is missing, not an error."""
    with pytest.raises(DiscordUIRuntimeMissing):
        DiscordUIRuntime.of(cast(Any, {"client": None}))


async def test_close_finishes_every_session_and_stops_answering_of() -> None:
    client = fake_client()
    runtime = install(cast(discord.Client, client))
    message_root = runtime.mount(Panel(), access=sd.Everyone(), timeout=None)
    opened = await runtime.sessions.open(message_root, delivered_to(message_harness()))
    assert isinstance(opened, sd.sessions.Opened)

    await runtime.close()

    assert message_root.finished
    assert tuple(runtime.sessions.active()) == ()
    with pytest.raises(DiscordUIRuntimeMissing):
        DiscordUIRuntime.of(cast(Any, client))


async def test_close_drops_the_installation_even_when_a_teardown_fails() -> None:
    client = fake_client()
    runtime = install(cast(discord.Client, client))
    root = runtime.mount(Panel(), access=sd.Everyone(), timeout=None)
    await runtime.sessions.open(root, delivered_to(message_harness()))
    root.on_finish(AsyncCallRecorder(error=RuntimeError("gateway is gone")))

    with pytest.raises(ExceptionGroup):
        await runtime.close()

    assert client not in _INSTALLED


async def test_run_serves_the_reactor_and_the_challenge_runner_together() -> None:
    runtime = install(cast(discord.Client, fake_client()), sd.Config(bus=LocalTopicBus()))
    assert runtime.scheduler is not None

    async with anyio.create_task_group() as tasks:
        await tasks.start(runtime.run)
        assert runtime.scheduler._running
        assert runtime.challenges._running
        assert runtime.execution_host is not None
        runtime.execution_host._require_running()
        tasks.cancel_scope.cancel()


async def test_run_serves_a_refresh_the_host_reactor_queued() -> None:
    """The scheduler `install` built is the one `run` drains, not a spare."""
    bus = LocalTopicBus()
    runtime = install(cast(discord.Client, fake_client()), sd.Config(bus=bus))
    assert runtime.scheduler is not None
    message_root = runtime.mount(Panel(), access=sd.Everyone(), timeout=None)
    message_root.refresh = AsyncCallRecorder()  # pyrefly: ignore
    runtime.scheduler.follow(message_root, Topic("build", "42"))

    task = asyncio.create_task(runtime.run())
    try:
        bus.publish(Topic("build", "42"))
        for _ in range(20):
            await asyncio.sleep(0)
            if message_root.refresh.await_count:
                break
    finally:
        task.cancel()
    assert message_root.refresh.records


async def test_close_drops_installation_when_execution_host_cleanup_fails() -> None:
    client = fake_client()
    runtime = install(cast(discord.Client, client))
    assert runtime.execution_host is not None
    runtime.execution_host.close = AsyncCallRecorder(error=RuntimeError("service host cleanup failed"))

    with pytest.raises(RuntimeError, match="service host cleanup failed"):
        await runtime.close()

    assert client not in _INSTALLED
