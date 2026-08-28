"""Declarative Screen policy over Invocation's opening primitives."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_reactivity import LocalTopicBus
from squid_ui.text import Message
from squid_ui_discord.sessions import AdmissionSpec, Opened, Reject
from squid_ui_discord.testing import fake_interaction, fake_message


class FakeClient:
    """A weak-referenceable installed client double."""


def _context(client: FakeClient) -> Any:
    return SimpleNamespace(
        bot=client,
        author=SimpleNamespace(id=7),
        guild=SimpleNamespace(id=42),
        interaction=None,
        send=AsyncMock(return_value=fake_message()),
    )


class BasicScreen(sd.Screen):
    session = "basic"

    def render(self):
        return sl.heading("Basic")


def test_spec_derives_and_caches_the_declared_session_policy() -> None:
    policy = AdmissionSpec(limit=2, collision=Reject(notice=Message("Full")))

    class Declared(sd.Screen):
        session = "declared"
        scope = sd.ScopeKind.USER_GUILD
        admission = policy
        capacity = 4
        quota = 1
        domain = "games"
        timeout = 30
        expiry = sd.PauseUpdates(10)
        options = {"strict": True}

        def render(self):
            return sl.heading("Declared")

    spec = Declared.spec()

    assert Declared.spec() is spec
    assert spec.name == "declared"
    assert spec.scope is sd.ScopeKind.USER_GUILD
    assert spec.admission is policy
    assert spec.capacity == 4
    assert spec.quota == 1
    assert spec.domain == "games"
    assert spec.options == {"strict": True, "timeout": 30, "expiry": sd.PauseUpdates(10)}


def test_a_sessionless_screen_has_no_session_spec() -> None:
    class Plain(sd.Screen):
        def render(self):
            return sl.heading("Plain")

    with pytest.raises(TypeError, match="mounts it directly"):
        Plain.spec()


async def test_show_sets_opening_and_prepares_before_the_first_render() -> None:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    context = _context(client)
    order: list[str] = []

    class Prepared(sd.Screen):
        session = "prepared"

        def __init__(self, label: str) -> None:
            order.append(f"construct:{label}")
            self.label = label

        async def prepare(self) -> None:
            assert self.opening.source is context
            order.append("prepare")

        def render(self):
            order.append("render")
            return sl.heading(self.label)

    screen = await Prepared("ready").show(context)

    assert isinstance(screen, Prepared)
    assert order == ["construct:ready", "prepare", "render"]
    assert screen.opening.runtime is runtime


async def test_show_returns_none_after_invocation_renders_a_rejection_notice() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    context = _context(client)

    class Exclusive(BasicScreen):
        session = "exclusive"
        admission = AdmissionSpec(collision=Reject(notice=Message("Already open")))

    first = await Exclusive().show(context)
    second = await Exclusive().show(context)

    assert isinstance(first, Exclusive)
    assert second is None
    assert context.send.await_count == 2


async def test_show_attaches_below_a_parent_session() -> None:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    context = _context(client)
    invocation = await sd.Invocation.of(context)
    parent_result = await invocation.open(BasicScreen(), sd.SessionSpec("parent"))
    assert isinstance(parent_result, Opened)

    child = await BasicScreen().show(invocation, parent=parent_result.session.root)

    assert isinstance(child, BasicScreen)
    child_root = parent_result.session.message_roots[-1]
    assert runtime.sessions.session_for(child_root) is parent_result.session
    assert parent_result.session.parent_of(child_root) is parent_result.session.root


async def test_show_passes_a_custom_session_key_through_invocation() -> None:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    context = _context(client)
    key = sd.SessionKey.custom("build-edit", (7, 99))

    screen = await BasicScreen().show(context, key=key)

    assert isinstance(screen, BasicScreen)
    assert len(runtime.sessions.get(key)) == 1


async def test_renew_ephemeral_degrades_without_an_installed_scheduler() -> None:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    context = _context(client)

    class Renewable(sd.Screen):
        session = "renewable"
        expiry = sd.RenewEphemeral(45)

        def render(self):
            return sl.heading("Renewable")

    screen = await Renewable().show(context)

    assert isinstance(screen, Renewable)
    session = runtime.sessions.get(sd.SessionKey.user("renewable", 7))[0]
    assert session.root.expiry == sd.PauseUpdates(45)


async def test_follow_topics_selects_the_installed_scheduler() -> None:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client), bus=LocalTopicBus())
    context = _context(client)

    class Following(sd.Screen):
        session = "following"
        follow_topics = True

        def render(self):
            return sl.heading("Following")

    screen = await Following().show(context)

    assert isinstance(screen, Following)
    session = runtime.sessions.get(sd.SessionKey.user("following", 7))[0]
    assert session.root.scheduler is runtime.scheduler


async def test_sessionless_show_uses_a_plain_owner_mount() -> None:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    interaction = fake_interaction(user_id=7)
    interaction.client = client
    interaction.guild = SimpleNamespace(id=42)

    class Plain(sd.Screen):
        visibility = "personal"
        timeout = None

        def render(self):
            return sl.heading("Plain")

    screen = await Plain().show(interaction)

    assert isinstance(screen, Plain)
    assert tuple(runtime.sessions.active()) == ()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_one_screen_instance_cannot_be_shown_twice() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    screen = BasicScreen()

    await screen.show(_context(client))

    with pytest.raises(RuntimeError, match="BasicScreen has already been shown"):
        await screen.show(_context(client))
