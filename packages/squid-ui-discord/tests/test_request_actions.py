"""Acknowledgement, form, and component-action response boundaries."""

from typing import Any, cast

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui.interactions import ActionEvent, Actor
from squid_ui_discord.actions import ActionResponder
from squid_ui_discord.testing import InteractionHarness


class FakeClient:
    pass


class Owner:
    pass


class Panel(sl.Component[sl.ComponentsV2Target]):
    def render(self):
        return sl.heading("Panel")


def installed() -> tuple[sd.DiscordUI[Any], InteractionHarness]:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    interaction = InteractionHarness(user_id=7)
    cast(Any, interaction).client = client
    return runtime.scope(Owner()), interaction


async def test_managed_private_defer_uses_inherited_response_without_conflict() -> None:
    ui, interaction = installed()
    request = await sd.DiscordRequest.create(ui, interaction.source, acknowledgement="private")

    await request.defer()
    outcome = await request.respond("Done")

    assert isinstance(outcome, sd.Sent)
    assert interaction.response.defer.await_args.kwargs == {"ephemeral": True, "thinking": True}
    interaction.edit_original_response.assert_awaited_once()


async def test_only_explicit_audience_conflicts_with_managed_defer() -> None:
    ui, interaction = installed()
    request = await sd.DiscordRequest.create(ui, interaction.source, acknowledgement="private")
    await request.defer()

    with pytest.raises(RuntimeError, match="audience conflicts"):
        await request.respond("Done", audience="public")


async def test_raw_response_becomes_a_followup() -> None:
    ui, interaction = installed()
    request = await ui.resolve(interaction.source)
    await interaction.response.send_message(content="native")
    interaction.followup.send.result = interaction.message

    outcome = await request.respond("facade")

    assert isinstance(outcome, sd.Sent)
    interaction.followup.send.assert_awaited_once()


async def test_unmanaged_defer_is_not_claimed() -> None:
    ui, interaction = installed()
    request = await ui.resolve(interaction.source)
    await interaction.response.defer(ephemeral=True, thinking=True)
    interaction.response.type = discord.InteractionResponseType.deferred_channel_message

    with pytest.raises(RuntimeError, match="deferred outside"):
        await request.respond("facade")


async def test_native_form_reserves_initial_response() -> None:
    ui, interaction = installed()
    request = await ui.resolve(interaction.source)
    modal = discord.ui.Modal(title="Rename")

    await request.open_form(modal)

    assert interaction.modals[0].args == (modal,)
    with pytest.raises(RuntimeError, match="form response"):
        await request.respond("extra")


async def test_form_reserved_request_cannot_defer() -> None:
    ui, interaction = installed()
    request = await sd.DiscordRequest.create(ui, interaction.source, acknowledgement="form")

    with pytest.raises(RuntimeError, match="form-reserved"):
        await request.defer()


async def test_discord_action_reuses_the_request_ledger() -> None:
    ui, interaction = installed()
    root = ui.runtime.mount(Panel(), access=sd.Owner(7))
    event = ActionEvent(
        actor=Actor("7"),
        responder=ActionResponder(cast(Any, interaction.source), root),
    )

    action = ui.action(event)
    await action.defer("private")
    outcome = await action.respond("Done")

    assert action.owner is ui.owner
    assert action.interaction is interaction.source
    assert isinstance(outcome, sd.Sent)
    interaction.edit_original_response.assert_awaited_once()
