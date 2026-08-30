"""Declarative Screen policy presented only through owner-scoped facades."""

from typing import Any, cast

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_reactivity import LocalTopicBus
from squid_ui.text import Message
from squid_ui_discord.sessions import AdmissionSpec, Reject
from squid_ui_discord.testing import ContextHarness, interaction_harness, message_harness


class FakeClient:
    """A weak-referenceable installed client double."""


class Owner:
    pass


def _context(client: FakeClient) -> Any:
    context = ContextHarness(message=message_harness(guild_id=42), bot=client, user_id=7)
    context.guild = context.message.guild
    return cast(Any, context)


def _ui(*, bus: LocalTopicBus | None = None) -> tuple[sd.DiscordUI[Owner], FakeClient]:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client), bus=bus)
    return runtime.scope(Owner()), client


class BasicScreen(sd.Screen[Owner]):
    session = sd.SessionSpec("basic")

    def render(self):
        return sl.heading("Basic")


def test_screen_compiles_its_class_policy_once() -> None:
    policy = AdmissionSpec(limit=2, collision=Reject(notice=Message("Full")))

    class Declared(sd.Screen[Owner]):
        audience = "public"
        access = sd.Everyone()
        session = sd.SessionSpec(
            "declared",
            scope=sd.ScopeKind.USER_GUILD,
            admission=policy,
            capacity=4,
            quota=1,
            domain="games",
        )
        timeout = 30
        expiry = sd.PauseUpdates(10)
        root_options = {"strict": True}

        def render(self):
            return sl.heading("Declared")

    compiled = Declared.__response_spec__
    Declared.timeout = 90

    assert compiled.audience == "public"
    assert compiled.access == sd.Everyone()
    assert compiled.timeout == 30
    assert compiled.session is Declared.session
    assert compiled.root_options == {"strict": True}
    assert Declared.__response_spec__.timeout == 30


def test_screen_has_no_delivery_method() -> None:
    assert not hasattr(BasicScreen(), "show")


def test_screen_rejects_duplicated_root_policy_at_class_creation() -> None:
    with pytest.raises(TypeError, match="repeats dedicated Screen policy: timeout"):

        class Invalid(sd.Screen[Owner]):
            root_options = {"timeout": 10}  # pyrefly: ignore[bad-typed-dict-key]

            def render(self):
                return sl.heading("Invalid")


async def test_facade_sets_opening_and_loads_before_first_render() -> None:
    ui, client = _ui()
    context = _context(client)
    order: list[str] = []

    class Loaded(sd.Screen[Owner]):
        session = sd.SessionSpec("loaded")

        def __init__(self, label: str) -> None:
            order.append(f"construct:{label}")
            self.label = label

        async def on_load(self) -> None:
            assert self.opening.source is context
            assert self.opening.owner is ui.owner
            order.append("load")

        def render(self):
            order.append("render")
            return sl.heading(self.label)

    outcome = await ui.respond(context, Loaded("ready"))

    assert isinstance(outcome, sd.Presented)
    assert order == ["construct:ready", "load", "render"]
    assert outcome.component.opening.runtime is ui.runtime


async def test_rejected_session_delivers_notice_without_loading_component() -> None:
    ui, client = _ui()
    context = _context(client)
    loads = 0

    class Exclusive(sd.Screen[Owner]):
        session = sd.SessionSpec(
            "exclusive",
            admission=AdmissionSpec(collision=Reject(notice=Message("Already open"))),
        )

        async def on_load(self) -> None:
            nonlocal loads
            loads += 1

        def render(self):
            return sl.heading("Exclusive")

    first = await ui.respond(context, Exclusive())
    second = await ui.respond(context, Exclusive())

    assert isinstance(first, sd.Presented)
    assert isinstance(second, sd.Rejected)
    assert loads == 1
    assert context.send.await_count == 2


async def test_facade_attaches_below_parent_session() -> None:
    ui, client = _ui()
    context = _context(client)
    parent = await ui.respond(context, BasicScreen())
    assert isinstance(parent, sd.Presented)

    child = await ui.respond(context, BasicScreen(), parent=parent.root)

    assert isinstance(child, sd.Presented)
    assert child.session is parent.session
    assert child.session is not None
    assert child.session.parent_of(child.root) is parent.root


async def test_facade_passes_a_custom_session_key() -> None:
    ui, client = _ui()
    key = sd.SessionKey.custom("build-edit", (7, 99))

    outcome = await ui.respond(_context(client), BasicScreen(), session_key=key)

    assert isinstance(outcome, sd.Presented)
    assert len(ui.runtime.sessions.get(key)) == 1


async def test_renew_ephemeral_degrades_without_scheduler() -> None:
    ui, client = _ui()

    class Renewable(sd.Screen[Owner]):
        session = sd.SessionSpec("renewable")
        expiry = sd.RenewEphemeral(45)

        def render(self):
            return sl.heading("Renewable")

    outcome = await ui.respond(_context(client), Renewable())

    assert isinstance(outcome, sd.Presented)
    assert outcome.root.expiry == sd.PauseUpdates(45)


async def test_follow_topics_selects_installed_scheduler() -> None:
    ui, client = _ui(bus=LocalTopicBus())

    class Following(sd.Screen[Owner]):
        session = sd.SessionSpec("following")
        follow_topics = True

        def render(self):
            return sl.heading("Following")

    outcome = await ui.respond(_context(client), Following())

    assert isinstance(outcome, sd.Presented)
    assert outcome.root.scheduler is ui.runtime.scheduler


async def test_sessionless_screen_uses_personal_invoker_mount() -> None:
    ui, client = _ui()
    interaction = interaction_harness(user_id=7)
    interaction.client = client
    interaction.guild = message_harness(guild_id=42).guild

    class Plain(sd.Screen[Owner]):
        timeout = None

        def render(self):
            return sl.heading("Plain")

    outcome = await ui.respond(interaction, Plain())

    assert isinstance(outcome, sd.Presented)
    assert tuple(ui.runtime.sessions.active()) == ()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    assert outcome.root.access == sd.Owner(7)


async def test_call_policy_overrides_screen_policy() -> None:
    ui, client = _ui()

    outcome = await ui.respond(
        _context(client),
        BasicScreen(),
        spec=sd.ResponseSpec(timeout=40),
        timeout=20,
    )

    assert isinstance(outcome, sd.Presented)
    assert outcome.root.timeout == 20


async def test_one_screen_instance_cannot_be_presented_twice() -> None:
    ui, client = _ui()
    screen = BasicScreen()

    await ui.respond(_context(client), screen)

    with pytest.raises(RuntimeError, match="already been presented"):
        await ui.respond(_context(client), screen)


async def test_parent_and_key_are_mutually_exclusive() -> None:
    ui, client = _ui()
    context = _context(client)
    parent = await ui.respond(context, BasicScreen())
    assert isinstance(parent, sd.Presented)

    with pytest.raises(TypeError, match="cannot be combined"):
        await ui.respond(context, BasicScreen(), parent=parent.root, session_key="ambiguous")
