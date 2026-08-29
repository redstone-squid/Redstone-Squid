"""Declarative Screen policy over Invocation's opening primitives."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
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
    session_name = "basic"

    def render(self):
        return sl.heading("Basic")


def test_screen_derives_its_declared_session_policy() -> None:
    policy = AdmissionSpec(limit=2, collision=Reject(notice=Message("Full")))

    class Declared(sd.Screen):
        session_name = "declared"
        scope = sd.ScopeKind.USER_GUILD
        admission = policy
        capacity = 4
        quota = 1
        domain = "games"
        timeout = 30
        expiry = sd.PauseUpdates(10)
        root_options = {"strict": True}

        def render(self):
            return sl.heading("Declared")

    access = sd.Everyone()
    spec = Declared()._session_spec(access)

    assert spec.name == "declared"
    assert spec.scope is sd.ScopeKind.USER_GUILD
    assert spec.admission is policy
    assert spec.capacity == 4
    assert spec.quota == 1
    assert spec.domain == "games"
    assert spec.access(sd.OpenContext(7)) is access


def test_a_direct_screen_rejects_session_only_policy_at_class_creation() -> None:
    with pytest.raises(TypeError, match=r"declares no session_name.*capacity"):

        class Invalid(sd.Screen):
            capacity = 4

            def render(self):
                return sl.heading("Invalid")


async def test_show_sets_opening_and_loads_before_the_first_render() -> None:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    context = _context(client)
    order: list[str] = []

    class Loaded(sd.Screen):
        session_name = "loaded"

        def __init__(self, label: str) -> None:
            order.append(f"construct:{label}")
            self.label = label

        async def on_load(self) -> None:
            assert self.opening.source is context
            order.append("load")

        def render(self):
            order.append("render")
            return sl.heading(self.label)

    screen = await Loaded("ready").show(context)

    assert isinstance(screen, Loaded)
    assert order == ["construct:ready", "load", "render"]
    assert screen.opening.runtime is runtime


async def test_show_returns_none_after_invocation_renders_a_rejection_notice() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    context = _context(client)
    loads = 0

    class Exclusive(BasicScreen):
        session_name = "exclusive"
        admission = AdmissionSpec(collision=Reject(notice=Message("Already open")))

        async def on_load(self) -> None:
            nonlocal loads
            loads += 1

    first = await Exclusive().show(context)
    second = await Exclusive().show(context)

    assert isinstance(first, Exclusive)
    assert second is None
    assert loads == 1
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
        session_name = "renewable"
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
        session_name = "following"
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
    assert sd.message_roots()[-1].access == sd.Owner(7)


async def test_sessionless_show_forwards_wait_to_interaction_delivery() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    interaction = fake_interaction(user_id=7)
    interaction.client = client

    class Plain(sd.Screen):
        def render(self):
            return sl.heading("Plain")

    await Plain().show(interaction, wait=True)

    interaction.original_response.assert_awaited_once()


@pytest.mark.parametrize("session_name", [None, "shared"])
async def test_fixed_access_applies_to_both_opening_paths(session_name: str | None) -> None:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    context = _context(client)

    class Shared(sd.Screen):
        access = sd.Everyone()

        def render(self):
            return sl.heading("Shared")

    if session_name is None:
        screen_type = Shared
    else:

        class SessionShared(Shared):
            session_name = "shared"

        screen_type = SessionShared
    screen = await screen_type().show(context)

    assert isinstance(screen, screen_type)
    if session_name is None:
        assert sd.message_roots()[-1].access == sd.Everyone()
    else:
        session = runtime.sessions.get(sd.SessionKey.user(session_name, 7))[0]
        assert session.root.access == sd.Everyone()


async def test_resolve_access_can_use_instance_state_and_opening() -> None:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    context = _context(client)

    class SharedWith(sd.Screen):
        session_name = "shared-with"

        def __init__(self, guest_id: int) -> None:
            self.guest_id = guest_id

        def resolve_access(self, opening: sd.Invocation) -> sd.AccessPolicy:
            return sd.Users({opening.user.id, self.guest_id})

        def render(self):
            return sl.heading("Shared")

    screen = await SharedWith(9).show(context)

    assert isinstance(screen, SharedWith)
    session = runtime.sessions.get(sd.SessionKey.user("shared-with", 7))[0]
    assert session.root.access == sd.Users({7, 9})


async def test_direct_screen_rejects_session_arguments_without_consuming_the_instance() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))

    class Plain(sd.Screen):
        def render(self):
            return sl.heading("Plain")

    screen = Plain()
    with pytest.raises(TypeError, match="key= cannot apply"):
        await screen.show(_context(client), key="ignored")

    assert await screen.show(_context(client)) is screen


async def test_parent_and_key_are_mutually_exclusive() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    invocation = await sd.Invocation.of(_context(client))
    parent = await invocation.mount(BasicScreen(), access=sd.Owner(7))

    with pytest.raises(TypeError, match="cannot be combined"):
        await BasicScreen().show(invocation, parent=parent, key="ambiguous")


async def test_one_screen_instance_cannot_be_shown_twice() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    screen = BasicScreen()

    await screen.show(_context(client))

    with pytest.raises(RuntimeError, match=r"BasicScreen\.show\(\) has already been called"):
        await screen.show(_context(client))


async def test_one_screen_instance_cannot_be_shown_concurrently() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    entered = anyio.Event()
    release = anyio.Event()

    class Slow(sd.Screen):
        async def on_load(self) -> None:
            entered.set()
            await release.wait()

        def render(self):
            return sl.heading("Slow")

    screen = Slow()

    async def show() -> None:
        await screen.show(_context(client))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(show)
        await entered.wait()
        with pytest.raises(RuntimeError, match="already been called"):
            await screen.show(_context(client))
        release.set()
