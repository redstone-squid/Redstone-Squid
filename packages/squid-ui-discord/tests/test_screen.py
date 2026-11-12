"""Declarative Screen policy presented only through owner-scoped facades."""

from typing import Any, cast, override

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_i18n import tr
from squid_reactivity import LocalTopicBus
from squid_ui.chrome import Chrome
from squid_ui_discord.response import UNSET
from squid_ui_discord.sessions import AdmissionSpec, Reject
from squid_ui_discord.testing import ContextHarness, interaction_harness, message_harness

CHROME = Chrome()


class FakeClient:
    """A weak-referenceable installed client double."""


class Owner:
    pass


def _context(client: FakeClient) -> Any:
    context = ContextHarness(message=message_harness(guild_id=42), bot=client, user_id=7)
    context.guild = context.message.guild
    return cast(Any, context)


def _ui(*, bus: LocalTopicBus | None = None) -> tuple[sd.Scope[Owner], FakeClient]:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client), sd.Config(bus=bus))
    return runtime.scope(Owner()), client


class BasicScreen(sd.Screen[None, Owner]):
    policy = sd.ResponseSpec(session=sd.SessionSpec("basic"))

    @override
    def render(self):
        return sl.heading("Basic")


def test_screen_compiles_its_class_policy_once() -> None:
    admission = AdmissionSpec(limit=2, collision=Reject(notice=tr(t"test-full = Full")))

    class Declared(sd.Screen[None, Owner]):
        policy = sd.ResponseSpec(
            audience="public",
            access=sd.Everyone(),
            session=sd.SessionSpec(
                "declared", scope=sd.ScopeKind.USER_GUILD, admission=admission, capacity=4, quota=1, domain="games"
            ),
            timeout=30,
            expiry=sd.PauseUpdates(10),
            chrome=CHROME,
        )

        @override
        def render(self):
            return sl.heading("Declared")

    compiled = Declared.__response_spec__
    declared_session = Declared.policy.session
    Declared.policy = sd.ResponseSpec(timeout=90)

    assert compiled.audience == "public"
    assert compiled.access == sd.Everyone()
    assert compiled.timeout == 30
    assert compiled.session is declared_session
    assert compiled.chrome is CHROME
    assert Declared.__response_spec__.timeout == 30


def test_screen_has_no_delivery_method() -> None:
    assert not hasattr(BasicScreen(), "show")


@pytest.mark.parametrize("timeout", [False, True])
async def test_expired_is_available_during_final_render(timeout: bool) -> None:
    rendered: list[bool] = []
    reasons: list[sd.CloseReason | None] = []

    class ExpiryScreen(sd.Screen):
        @override
        def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
            rendered.append(self.expired)
            reasons.append(self.close_reason)
            return sl.heading("Expired" if self.expired else "Active")

    ui, client = _ui()
    screen = ExpiryScreen()
    assert not screen.expired
    request = await ui.request(_context(client))
    result = await request.respond(screen)
    assert isinstance(result, sd.Presented)
    assert rendered == [False]
    if timeout:
        await result.root.handle_timeout()
    else:
        await result.root.finish()
    assert rendered[-1] is timeout
    assert screen.expired is timeout
    assert reasons == [None, sd.CloseReason.TIMEOUT if timeout else sd.CloseReason.CLOSED]


async def test_facade_sets_opening_and_loads_before_first_render() -> None:
    ui, client = _ui()
    context = _context(client)
    order: list[str] = []

    class Loaded(sd.Screen[None, Owner]):
        policy = sd.ResponseSpec(session=sd.SessionSpec("loaded"))

        def __init__(self, label: str) -> None:
            order.append(f"construct:{label}")
            self.label = label

        @override
        async def on_load(self) -> None:
            assert self.opening is not None
            assert self.opening.source is context
            assert self.opening.owner is ui.owner
            order.append("load")

        @override
        def render(self):
            order.append("render")
            return sl.heading(self.label)

    request = await ui.request(context)
    outcome = await request.respond(Loaded("ready"))

    assert isinstance(outcome, sd.Presented)
    assert order == ["construct:ready", "load", "render"]
    assert outcome.component.opening is not None
    assert outcome.component.opening.runtime is ui.runtime


async def test_rejected_session_delivers_notice_without_loading_component() -> None:
    ui, client = _ui()
    context = _context(client)
    loads = 0

    class Exclusive(sd.Screen[None, Owner]):
        policy = sd.ResponseSpec(
            session=sd.SessionSpec(
                "exclusive", admission=AdmissionSpec(collision=Reject(notice=tr(t"test-already-open = Already open")))
            )
        )

        @override
        async def on_load(self) -> None:
            nonlocal loads
            loads += 1

        @override
        def render(self):
            return sl.heading("Exclusive")

    request = await ui.request(context)
    first = await request.respond(Exclusive())
    second = await request.respond(Exclusive())

    assert isinstance(first, sd.Presented)
    assert isinstance(second, sd.Rejected)
    assert loads == 1
    assert context.send.await_count == 2


async def test_facade_attaches_below_parent_session() -> None:
    ui, client = _ui()
    context = _context(client)
    request = await ui.request(context)
    parent = await request.respond(BasicScreen())
    assert isinstance(parent, sd.Presented)

    child = await request.respond(BasicScreen(), parent=parent.root)

    assert isinstance(child, sd.Presented)
    assert child.session is parent.session
    assert child.session is not None
    assert child.session.parent_of(child.root) is parent.root


async def test_facade_passes_a_custom_session_key() -> None:
    ui, client = _ui()
    key = sd.SessionKey.custom("build-edit", (7, 99))

    request = await ui.request(_context(client))
    outcome = await request.respond(BasicScreen(), session_key=key)

    assert isinstance(outcome, sd.Presented)
    assert len(ui.runtime.sessions.get(key)) == 1


async def test_renew_ephemeral_degrades_without_scheduler() -> None:
    ui, client = _ui()

    class Renewable(sd.Screen[None, Owner]):
        policy = sd.ResponseSpec(session=sd.SessionSpec("renewable"), expiry=sd.RenewEphemeral(45))

        @override
        def render(self):
            return sl.heading("Renewable")

    request = await ui.request(_context(client))
    outcome = await request.respond(Renewable())

    assert isinstance(outcome, sd.Presented)
    assert outcome.root.expiry == sd.PauseUpdates(45)


@pytest.mark.parametrize("bus", [None, LocalTopicBus()])
async def test_live_updates_selects_installed_scheduler(bus: LocalTopicBus | None) -> None:
    ui, client = _ui(bus=bus)

    class Following(sd.Screen[None, Owner]):
        policy = sd.ResponseSpec(session=sd.SessionSpec("following"), live_updates=True)

        @override
        def render(self):
            return sl.heading("Following")

    request = await ui.request(_context(client))
    outcome = await request.respond(Following())

    assert isinstance(outcome, sd.Presented)
    assert outcome.root.scheduler is ui.runtime.scheduler


async def test_sessionless_screen_uses_personal_invoker_mount() -> None:
    ui, client = _ui()
    interaction = interaction_harness(user_id=7)
    interaction.client = client
    interaction.guild = message_harness(guild_id=42).guild

    class Plain(sd.Screen[None, Owner]):
        policy = sd.ResponseSpec(timeout=None)

        @override
        def render(self):
            return sl.heading("Plain")

    request = await ui.request(interaction)
    outcome = await request.respond(Plain())

    assert isinstance(outcome, sd.Presented)
    assert tuple(ui.runtime.sessions.active()) == ()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    assert outcome.root.access == sd.Owner(7)


async def test_call_policy_overrides_screen_policy() -> None:
    ui, client = _ui()

    request = await ui.request(_context(client))
    outcome = await request.respond(
        sd.Response(BasicScreen(), timeout=40),
        timeout=20,
    )

    assert isinstance(outcome, sd.Presented)
    assert outcome.root.timeout == 20


async def test_one_screen_instance_cannot_be_presented_twice() -> None:
    ui, client = _ui()
    screen = BasicScreen()

    request = await ui.request(_context(client))
    await request.respond(screen)

    request = await ui.request(_context(client))
    with pytest.raises(RuntimeError, match="one presentation attempt"):
        await request.respond(screen)


async def test_parent_and_key_are_mutually_exclusive() -> None:
    ui, client = _ui()
    context = _context(client)
    request = await ui.request(context)
    parent = await request.respond(BasicScreen())
    assert isinstance(parent, sd.Presented)

    with pytest.raises(TypeError, match="cannot be combined"):
        await request.respond(BasicScreen(), parent=parent.root, session_key="ambiguous")


class PersonalSession(sd.Screen[None, Owner]):
    policy = sd.ResponseSpec(session=sd.SessionSpec("personal"))

    @override
    def render(self) -> sl.LayoutNode:
        return sl.heading("Personal")


class GuildPersonalSession(sd.Screen[None, Owner]):
    policy = sd.ResponseSpec(session=sd.SessionSpec("guild-personal", scope=sd.ScopeKind.USER_GUILD))

    @override
    def render(self) -> sl.LayoutNode:
        return sl.heading("Guild personal")


class SharedSession(sd.Screen[None, Owner]):
    policy = sd.ResponseSpec(
        audience="public",
        access=sd.Everyone(),
        session=sd.SessionSpec("shared", scope=sd.ScopeKind.GUILD, admission=AdmissionSpec(collision=Reject())),
    )

    @override
    def render(self) -> sl.LayoutNode:
        return sl.heading("Shared")


@pytest.mark.parametrize(
    ("screen_type", "scope", "public"),
    [
        (PersonalSession, sd.ScopeKind.USER, False),
        (GuildPersonalSession, sd.ScopeKind.USER_GUILD, False),
        (SharedSession, sd.ScopeKind.GUILD, True),
    ],
)
async def test_session_policy_applies_through_the_facade(
    screen_type: type[sd.Screen[None, Owner]], scope: sd.ScopeKind, public: bool
) -> None:
    ui, client = _ui()
    interaction = interaction_harness(user_id=7)
    interaction.client = client
    interaction.guild = message_harness(guild_id=42).guild
    request = await ui.request(interaction)
    first = await request.respond(screen_type())

    assert isinstance(first, sd.Presented)
    recipe = screen_type.__response_spec__.session
    assert isinstance(recipe, sd.SessionSpec)
    assert recipe.scope is scope
    assert first.session is not None
    assert first.component.session is first.session
    assert first.session.key == recipe.key(sd.OpenContext.of(cast(discord.Interaction, interaction)))
    assert first.root.access == (sd.Everyone() if public else sd.Owner(7))
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is (not public)

    second = await request.respond(screen_type())
    assert isinstance(second, sd.Rejected if public else sd.Presented)


def test_unnamed_session_policy_is_resolved_per_concrete_screen() -> None:
    recipe = sd.SessionSpec(scope=sd.ScopeKind.USER_GUILD, capacity=4, quota=1)

    class Parent(BasicScreen):
        policy = sd.ResponseSpec(session=recipe)

    class Child(Parent):
        pass

    parent_recipe = Parent.__response_spec__.session
    child_recipe = Child.__response_spec__.session
    assert isinstance(parent_recipe, sd.SessionSpec)
    assert isinstance(child_recipe, sd.SessionSpec)
    assert parent_recipe.name == f"{Parent.__module__}.{Parent.__qualname__}"
    assert child_recipe.name == f"{Child.__module__}.{Child.__qualname__}"
    assert parent_recipe.name != child_recipe.name
    assert parent_recipe.capacity == child_recipe.capacity == 4
    assert parent_recipe.quota == child_recipe.quota == 1
    assert parent_recipe.scope is child_recipe.scope is sd.ScopeKind.USER_GUILD
    assert recipe.name is None
    assert Parent.policy is Child.policy


def test_named_session_policy_retains_its_identity_when_inherited() -> None:
    recipe = sd.SessionSpec("shared-domain", scope=sd.ScopeKind.GLOBAL)

    class Parent(BasicScreen):
        policy = sd.ResponseSpec(session=recipe)

    class Child(Parent):
        pass

    assert Parent.__response_spec__.session is recipe
    assert Child.__response_spec__.session is recipe


def test_replacing_inherited_policy_uses_normal_value_semantics() -> None:
    class Parent(BasicScreen):
        policy = sd.ResponseSpec(audience="public", timeout=30, session=sd.SessionSpec("parent"))

    class Child(Parent):
        policy = sd.ResponseSpec(timeout=None)

    assert Child.policy.timeout is None
    assert Child.__response_spec__.audience == "personal"
    assert Child.__response_spec__.access is sd.invoker_only
    assert Child.__response_spec__.session is UNSET
    assert Child.__response_spec__.timeout is None


def test_unspecified_operational_policy_inherits_runtime_defaults() -> None:
    client = FakeClient()
    runtime = sd.install(
        cast(discord.Client, client),
        sd.Config(responses=sd.ResponseSpec(timeout=75, live_updates=True, retain_routed_on_timeout=True)),
    )
    ui = runtime.scope(Owner())

    class Plain(sd.Screen[None, Owner]):
        @override
        def render(self) -> sl.LayoutNode:
            return sl.heading("Plain")

    effective = ui._policy(Plain(), {}, actor_id=7)
    assert effective.timeout == 75
    assert effective.live_updates is True
    assert effective.retain_routed_on_timeout is True
    assert effective.audience == "personal"
    assert effective.access == sd.Owner(7)


async def test_unnamed_session_opens_under_its_resolved_key() -> None:
    ui, client = _ui()

    class Inferred(BasicScreen):
        policy = sd.ResponseSpec(session=sd.SessionSpec())

    request = await ui.request(_context(client))
    outcome = await request.respond(Inferred())

    assert isinstance(outcome, sd.Presented)
    assert outcome.session is not None
    recipe = Inferred.__response_spec__.session
    assert isinstance(recipe, sd.SessionSpec)
    assert outcome.session.key == recipe.key(sd.OpenContext(7, 42))
