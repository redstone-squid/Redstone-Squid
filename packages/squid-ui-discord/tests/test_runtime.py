"""Installing the Discord runtime on a client, and finding it again from a click."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_reactivity import LocalTopicBus
from squid_ui.primitives import Heading
from squid_ui.runtime.topics import Topic
from squid_ui_discord import ClientRuntime, install
from squid_ui_discord.runtime import _INSTALLED, ClientRuntimeMissing
from squid_ui_discord.testing import delivered_to, fake_interaction, fake_message


class Panel(sl.Component):
    def render(self):
        return [Heading("Panel")]


class FakeClient:
    """A client stand-in: the weak table only needs a weak-referenceable key."""


def fake_client() -> Any:
    return FakeClient()


@pytest.fixture(autouse=True)
def _forget_installations():
    """Installations are process-wide, so a leaked one would reach the next test."""
    yield
    _INSTALLED.clear()


def test_install_wires_the_presenter_the_registry_could_not_build_for_itself() -> None:
    client = fake_client()

    runtime = install(cast(discord.Client, client))

    presenter = runtime.defaults.challenge
    assert isinstance(presenter, sd.DialogPresenter)
    assert presenter.sessions is runtime.sessions
    assert presenter.supervisor is runtime.challenges
    # The registry's defaults and the host's are one object, so a mount opened through
    # either is wired the same way.
    assert runtime.defaults is runtime.sessions.defaults


def test_install_makes_a_reactor_only_when_given_a_bus() -> None:
    without = install(cast(discord.Client, fake_client()))
    with_bus = install(cast(discord.Client, fake_client()), bus=LocalTopicBus())

    assert without.scheduler is None
    assert without.defaults.scheduler is None
    assert with_bus.scheduler is not None
    assert with_bus.defaults.scheduler is with_bus.scheduler


def test_a_second_install_on_one_client_is_refused() -> None:
    """Two hosts on one client would give the same click two registries."""
    client = fake_client()
    install(cast(discord.Client, client))

    with pytest.raises(ValueError, match="already has a layout host"):
        install(cast(discord.Client, client))


def test_install_keeps_host_defaults_and_adds_to_them() -> None:
    chrome = sl.chrome.Chrome(close=sl.text.ResolvedText("Dismiss"))

    runtime = install(cast(discord.Client, fake_client()), defaults=sd.MessageRootDefaults(chrome=chrome, strict=True))

    assert runtime.defaults.chrome is chrome
    assert runtime.defaults.strict is True


def test_of_resolves_from_a_client_an_interaction_and_a_command_context() -> None:
    client = fake_client()
    runtime = install(cast(discord.Client, client))
    interaction = fake_interaction(user_id=7)
    interaction.client = client
    context = SimpleNamespace(bot=client, author=SimpleNamespace(id=7), send=AsyncMock())

    assert ClientRuntime.of(cast(Any, client)) is runtime
    assert ClientRuntime.of(interaction) is runtime
    assert ClientRuntime.of(cast(Any, context)) is runtime


def test_of_raises_rather_than_returning_none_when_nothing_is_installed() -> None:
    """A missing installation is a wiring bug, so every caller would grow the same raise."""
    with pytest.raises(ClientRuntimeMissing, match="no layout host is installed"):
        ClientRuntime.of(cast(Any, fake_client()))


def test_of_survives_a_source_that_cannot_be_a_weak_key() -> None:
    """An unhashable source was never a key here; it is missing, not an error."""
    with pytest.raises(ClientRuntimeMissing):
        ClientRuntime.of(cast(Any, {"client": None}))


def test_a_panel_holding_the_host_needs_no_other_object() -> None:
    runtime = install(cast(discord.Client, fake_client()))

    message_root = runtime.mount(Panel(), access=sd.Everyone(), timeout=None)

    assert message_root.challenge is runtime.defaults.challenge
    assert message_root.access == sd.Everyone()


async def test_close_finishes_every_session_and_stops_answering_of() -> None:
    client = fake_client()
    runtime = install(cast(discord.Client, client))
    message_root = runtime.mount(Panel(), access=sd.Everyone(), timeout=None)
    opened = await runtime.sessions.open(message_root, delivered_to(fake_message()))
    assert isinstance(opened, sd.sessions.Opened)

    await runtime.close()

    assert message_root.finished
    assert tuple(runtime.sessions.active()) == ()
    with pytest.raises(ClientRuntimeMissing):
        ClientRuntime.of(cast(Any, client))


async def test_close_drops_the_installation_even_when_a_teardown_fails() -> None:
    client = fake_client()
    runtime = install(cast(discord.Client, client))
    runtime.sessions.close_all = AsyncMock(side_effect=RuntimeError("gateway is gone"))

    with pytest.raises(RuntimeError):
        await runtime.close()

    assert client not in _INSTALLED


async def test_run_serves_the_reactor_and_the_challenge_runner_together() -> None:
    runtime = install(cast(discord.Client, fake_client()), bus=LocalTopicBus())
    assert runtime.scheduler is not None

    task = asyncio.create_task(runtime.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert runtime.scheduler._running
    assert runtime.challenges._running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_serves_a_refresh_the_host_reactor_queued() -> None:
    """The scheduler `install` built is the one `run` drains, not a spare."""
    bus = LocalTopicBus()
    runtime = install(cast(discord.Client, fake_client()), bus=bus)
    assert runtime.scheduler is not None
    message_root = runtime.mount(Panel(), access=sd.Everyone(), timeout=None)
    message_root.refresh = AsyncMock()  # pyrefly: ignore
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
    message_root.refresh.assert_awaited()
