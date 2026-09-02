"""Audience-aware static and live facade presentation."""

from typing import Any, cast

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui_discord.testing import InteractionHarness


class FakeClient:
    pass


class Owner:
    pass


class Panel(sl.Component[sl.ComponentsV2Target]):
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
    runtime = sd.install(cast(discord.Client, client), localization=localize)
    ui = runtime.scope(Owner())
    interaction = InteractionHarness(user_id=7)
    cast(Any, interaction).client = client

    outcome = await ui.respond(interaction.source, "Hello", audience="personal")

    assert isinstance(outcome, sd.Sent)
    assert calls == 1
    assert interaction.response.send_message.await_args.kwargs["content"] == "Hello"
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_live_response_returns_typed_presentation_and_tracks_owner() -> None:
    ui, interaction = installed()
    panel = Panel()

    outcome = await ui.respond(interaction.source, panel)

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
        sd.DiscordUIConfig(responses=sd.ResponseSpec(timeout=10, audience="public")),
    )
    ui = runtime.scope(Owner(), defaults=sd.ResponseSpec(timeout=20))

    class Timed(sd.Screen[Any]):
        timeout = 30

        def render(self):
            return sl.heading("Timed")

    plain = ui._policy(Panel(), {})
    assert plain.timeout == 20
    assert plain.audience == "public"

    assert ui._policy(Timed(), {}).timeout == 30
    assert ui._policy(Timed(), {"timeout": 40}).timeout == 40


async def test_private_interaction_is_ephemeral() -> None:
    ui, interaction = installed()

    await ui.respond(interaction.source, "Secret", audience=sd.Private("sensitive"))

    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
