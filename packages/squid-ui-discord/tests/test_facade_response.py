"""Audience-aware static and live facade presentation."""

from typing import Any, Literal, cast, override

import anyio
import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui_discord.response import UNSET
from squid_ui_discord.testing import InteractionHarness, stage


class FakeClient:
    pass


class Owner:
    pass


class Panel(sl.Component[sl.ComponentsV2Target]):
    @override
    def render(self):
        return sl.heading("Panel")


def installed() -> tuple[sd.Scope[Any], InteractionHarness]:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    interaction = InteractionHarness(user_id=7)
    cast(Any, interaction).client = client
    return runtime.scope(Owner()), interaction


async def test_static_response_returns_sent_and_resolves_localization_once() -> None:
    calls = 0

    async def localize(source: sd.contracts.LocalizationSource) -> sl.text.Localization:
        nonlocal calls
        del source
        calls += 1
        return sl.text.Localization(locale="en-GB")

    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client), sd.Config(localization=localize))
    ui = runtime.scope(Owner())
    interaction = InteractionHarness(user_id=7)
    cast(Any, interaction).client = client

    request = await ui.request(interaction.source)
    outcome = await request.respond("Hello", audience="personal")

    assert isinstance(outcome, sd.Sent)
    assert calls == 1
    assert interaction.response.send_message.await_args.kwargs["content"] == "Hello"
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_live_response_returns_typed_presentation_and_tracks_owner() -> None:
    ui, interaction = installed()
    panel = Panel()

    request = await ui.request(interaction.source)
    outcome = await request.respond(panel)

    assert isinstance(outcome, sd.Presented)
    assert outcome.component is panel
    await ui.close()
    assert outcome.root.finished


async def test_out_of_band_live_send_requires_explicit_access() -> None:
    ui, _ = installed()
    channel = cast(Any, type("Channel", (), {"send": None})())

    with pytest.raises(TypeError, match="identifiable actor"):
        await ui.send(channel, Panel())


def test_response_policy_overlays_in_documented_order() -> None:
    client = FakeClient()
    runtime = sd.install(
        cast(discord.Client, client),
        sd.Config(responses=sd.ResponseSpec(timeout=10, audience="public")),
    )
    ui = runtime.scope(Owner())

    class Timed(sd.Screen[None, Any]):
        policy = sd.ResponseSpec(timeout=30)

        @override
        def render(self):
            return sl.heading("Timed")

    plain = ui._policy(Panel(), {})
    assert plain.timeout == 10
    assert plain.audience == "public"

    assert ui._policy(Timed(), {}).timeout == 30
    assert ui._policy(Timed(), {"timeout": 40}).timeout == 40


@pytest.mark.parametrize(
    "screen_nav,call_nav,expected",
    [
        (UNSET, UNSET, sd.navigation_controls.page_select_nav),
        (sd.navigation_controls.page_jump_nav, UNSET, sd.navigation_controls.page_jump_nav),
        (sd.navigation_controls.page_jump_nav, sd.navigation_controls.default_nav, sd.navigation_controls.default_nav),
    ],
)
async def test_navigation_policy_reaches_the_presented_pager(screen_nav, call_nav, expected) -> None:
    class Pages(sd.Screen):
        policy = sd.ResponseSpec(nav=screen_nav)

        @override
        def render(self):
            return sl.primitives.Lines(
                tuple(str(index) for index in range(40)),
                overflow=sl.primitives.Paginate(key="rows", per=1),
            )

    config = sd.Config(responses=sd.ResponseSpec(nav=sd.navigation_controls.page_select_nav))
    overrides = {} if call_nav is UNSET else {"nav": call_nav}
    async with stage(Pages(), owner=object(), config=config, **overrides) as ui:
        assert ui.root.nav is expected
        assert ui.root.plan is not None
        assert ("__cursor_seek.rows.jump" in ui.root.plan.form_bindings) == (
            expected is sd.navigation_controls.page_jump_nav
        )


async def test_private_interaction_is_ephemeral() -> None:
    ui, interaction = installed()

    request = await ui.request(interaction.source)
    await request.respond("Secret", audience=sd.Private("sensitive"))

    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


@pytest.mark.parametrize("session", [None, sd.SessionSpec("failed-opening")])
@pytest.mark.parametrize("failure", ["error", "abandon", "cancel"])
async def test_failed_open_releases_scope_ownership_and_provisional_session(
    session: sd.SessionSpec | None, failure: Literal["error", "abandon", "cancel"]
) -> None:
    ui, interaction = installed()
    captured: list[sd.MessageRoot] = []
    cancellation = anyio.CancelScope()

    class Opening(Panel):
        @override
        async def on_load(self) -> None:
            captured.extend(ui._lifetime.roots())
            if failure == "cancel":
                cancellation.cancel()
                await anyio.lowlevel.checkpoint()

    request = await ui.request(interaction.source)
    if failure == "error":
        interaction.response.send_message.error = OSError("delivery failed")
        with pytest.raises(OSError, match="delivery failed"):
            await request.respond(Opening(), session=session)
    elif failure == "abandon":
        interaction.response.send_message.error = sd.delivery.DeliveryAbandoned()
        assert isinstance(await request.respond(Opening(), session=session), sd.Abandoned)
    else:
        with cancellation:
            await request.respond(Opening(), session=session)
    (root,) = captured
    assert root.finished
    assert not tuple(ui._lifetime.roots())
    assert ui.runtime.sessions.session_for(root) is None
    await root.wait()
