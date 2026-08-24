"""Installing the Discord runtime on a client, and finding it again from a click."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import LayoutHost, install
from squid_layouts.discord.host import _INSTALLED, LayoutHostMissing
from squid_layouts.discord.testing import delivered_to, fake_interaction, fake_message
from squid_layouts.primitives import Heading
from squid_layouts.runtime.topics import Topic
from squid_reactive import LocalTopicBus


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

    host = install(cast(discord.Client, client))

    presenter = host.defaults.challenge
    assert isinstance(presenter, sl.discord.DialogPresenter)
    assert presenter.sessions is host.mounts
    assert presenter.supervisor is host.challenges
    # The registry's defaults and the host's are one object, so a mount opened through
    # either is wired the same way.
    assert host.defaults is host.mounts.defaults


def test_install_makes_a_reactor_only_when_given_a_bus() -> None:
    without = install(cast(discord.Client, fake_client()))
    with_bus = install(cast(discord.Client, fake_client()), bus=LocalTopicBus())

    assert without.reactor is None
    assert without.defaults.scheduler is None
    assert with_bus.reactor is not None
    assert with_bus.defaults.scheduler is with_bus.reactor


def test_a_second_install_on_one_client_is_refused() -> None:
    """Two hosts on one client would give the same click two registries."""
    client = fake_client()
    install(cast(discord.Client, client))

    with pytest.raises(ValueError, match="already has a layout host"):
        install(cast(discord.Client, client))


def test_install_keeps_host_defaults_and_adds_to_them() -> None:
    chrome = sl.chrome.Chrome(close=sl.text.ResolvedText("Dismiss"))

    host = install(cast(discord.Client, fake_client()), defaults=sl.discord.MountDefaults(chrome=chrome, strict=True))

    assert host.defaults.chrome is chrome
    assert host.defaults.strict is True


def test_of_resolves_from_a_client_an_interaction_and_a_command_context() -> None:
    client = fake_client()
    host = install(cast(discord.Client, client))
    interaction = fake_interaction(user_id=7)
    interaction.client = client
    context = SimpleNamespace(bot=client, author=SimpleNamespace(id=7), send=AsyncMock())

    assert LayoutHost.of(cast(Any, client)) is host
    assert LayoutHost.of(interaction) is host
    assert LayoutHost.of(cast(Any, context)) is host


def test_of_raises_rather_than_returning_none_when_nothing_is_installed() -> None:
    """A missing installation is a wiring bug, so every caller would grow the same raise."""
    with pytest.raises(LayoutHostMissing, match="no layout host is installed"):
        LayoutHost.of(cast(Any, fake_client()))


def test_of_survives_a_source_that_cannot_be_a_weak_key() -> None:
    """An unhashable source was never a key here; it is missing, not an error."""
    with pytest.raises(LayoutHostMissing):
        LayoutHost.of(cast(Any, {"client": None}))


def test_a_panel_holding_the_host_needs_no_other_object() -> None:
    host = install(cast(discord.Client, fake_client()))

    mount = host.mount(Panel(), access=sl.discord.Everyone(), timeout=None)

    assert mount.challenge is host.defaults.challenge
    assert mount.access == sl.discord.Everyone()


async def test_close_finishes_every_session_and_stops_answering_of() -> None:
    client = fake_client()
    host = install(cast(discord.Client, client))
    mount = host.mount(Panel(), access=sl.discord.Everyone(), timeout=None)
    opened = await host.mounts.open(mount, delivered_to(fake_message()))
    assert isinstance(opened, sl.discord.sessions.Opened)

    await host.close()

    assert mount.finished
    assert tuple(host.mounts.active()) == ()
    with pytest.raises(LayoutHostMissing):
        LayoutHost.of(cast(Any, client))


async def test_close_drops_the_installation_even_when_a_teardown_fails() -> None:
    client = fake_client()
    host = install(cast(discord.Client, client))
    host.mounts.close_all = AsyncMock(side_effect=RuntimeError("gateway is gone"))

    with pytest.raises(RuntimeError):
        await host.close()

    assert client not in _INSTALLED


async def test_run_serves_the_reactor_and_the_challenge_runner_together() -> None:
    host = install(cast(discord.Client, fake_client()), bus=LocalTopicBus())
    assert host.reactor is not None

    task = asyncio.create_task(host.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert host.reactor._running
    assert host.challenges._running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_serves_a_refresh_the_host_reactor_queued() -> None:
    """The reactor `install` built is the one `run` drains, not a spare."""
    bus = LocalTopicBus()
    host = install(cast(discord.Client, fake_client()), bus=bus)
    assert host.reactor is not None
    mount = host.mount(Panel(), access=sl.discord.Everyone(), timeout=None)
    mount.refresh_now = AsyncMock()  # pyrefly: ignore
    host.reactor.follow(mount, Topic("build", "42"))

    task = asyncio.create_task(host.run())
    try:
        bus.publish(Topic("build", "42"))
        for _ in range(20):
            await asyncio.sleep(0)
            if mount.refresh_now.await_count:
                break
    finally:
        task.cancel()
    mount.refresh_now.assert_awaited()
