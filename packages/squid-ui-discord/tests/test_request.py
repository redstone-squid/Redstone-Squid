"""One request per Discord event: resolution, memoization, and the response ledger."""

from typing import Any, cast

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui.interactions import ActionEvent, Actor
from squid_ui_discord.actions import ActionResponder
from squid_ui_discord.testing import ContextHarness, InteractionHarness


class FakeClient:
    pass


class Owner:
    pass


class Panel(sl.Component[sl.ComponentsV2Target]):
    def render(self):
        return sl.heading("Panel")


def installed() -> tuple[sd.Scope[Owner], InteractionHarness]:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    interaction = InteractionHarness(user_id=7, client=client)
    return runtime.scope(Owner()), interaction


async def test_request_normalizes_an_interaction() -> None:
    ui, interaction = installed()

    request = await ui.request(interaction.source)

    assert request.kind == "interaction"
    assert request.interaction is interaction.source
    assert request.context is None
    assert request.user.id == 7
    assert request.guild is interaction.guild
    assert request.owner is ui.owner
    assert request.runtime is ui.runtime
    assert request.localization is ui.runtime.defaults.localization


async def test_request_normalizes_a_command_context() -> None:
    ui, _ = installed()
    context = ContextHarness(bot=ui.runtime.client, user_id=3)

    request = await ui.request(context.source)

    assert request.kind == "context"
    assert request.context is context.source
    assert request.interaction is None
    assert request.user.id == 3


async def test_the_same_event_resolves_to_the_same_request() -> None:
    ui, interaction = installed()

    first = await ui.request(interaction.source)
    second = await sd.request(interaction.source)

    assert first is second
    assert first.scope is ui


async def test_an_unowned_request_lands_in_the_app_scope() -> None:
    ui, interaction = installed()

    request = await sd.request(interaction.source)

    assert request.scope is ui.runtime.app
    assert request.owner is ui.runtime.client


async def test_defer_fixes_the_audience_of_what_follows() -> None:
    ui, interaction = installed()
    request = await ui.request(interaction.source)

    await request.defer("private")
    outcome = await request.respond("Done")

    assert request.deferred == "private"
    assert isinstance(outcome, sd.Sent)
    assert interaction.response.defer.await_args.kwargs == {"ephemeral": True, "thinking": True}
    interaction.edit_original_response.assert_awaited_once()


async def test_only_an_explicit_audience_conflicts_with_a_defer() -> None:
    ui, interaction = installed()
    request = await ui.request(interaction.source)
    await request.defer("private")

    with pytest.raises(RuntimeError, match="audience conflicts"):
        await request.respond("Done", audience="public")


async def test_a_second_response_becomes_a_followup() -> None:
    ui, interaction = installed()
    request = await ui.request(interaction.source)
    await interaction.response.send_message(content="native")
    interaction.followup.send.result = interaction.message

    outcome = await request.respond("facade")

    assert isinstance(outcome, sd.Sent)
    interaction.followup.send.assert_awaited_once()


async def test_a_defer_outside_the_ledger_is_not_claimed() -> None:
    ui, interaction = installed()
    request = await ui.request(interaction.source)
    await interaction.response.defer(ephemeral=True, thinking=True)

    with pytest.raises(RuntimeError, match="deferred outside"):
        await request.respond("facade")
    with pytest.raises(RuntimeError, match="acknowledged outside"):
        await request.defer()


async def test_a_form_is_the_initial_response_and_excludes_content() -> None:
    ui, interaction = installed()
    request = await ui.request(interaction.source)
    modal = discord.ui.Modal(title="Rename")

    await request.form(modal)

    assert interaction.modals[0].args == (modal,)
    with pytest.raises(RuntimeError, match="form response"):
        await request.respond("extra")


async def test_a_form_cannot_follow_a_response() -> None:
    ui, interaction = installed()
    request = await ui.request(interaction.source)
    await request.respond("first")

    with pytest.raises(RuntimeError, match="initial response"):
        await request.form(discord.ui.Modal(title="Late"))


async def test_a_click_resolves_to_its_root_and_scope() -> None:
    ui, interaction = installed()
    root = ui.runtime.mount(Panel(), access=sd.Owner(7))
    ui.runtime._track(ui, root)
    event = ActionEvent(actor=Actor("7"), responder=ActionResponder(cast(Any, interaction.source), root))

    request = await sd.request(event)
    await request.defer("private")
    outcome = await request.respond("Done")

    assert request.root is root
    assert request.scope is ui
    assert request.interaction is interaction.source
    assert await sd.request(interaction.source) is request
    assert isinstance(outcome, sd.Sent)
    interaction.edit_original_response.assert_awaited_once()


async def test_a_foreign_frontend_event_is_refused() -> None:
    event = ActionEvent(actor=Actor("7"), responder=cast(Any, object()))

    with pytest.raises(LookupError, match="not Discord"):
        await sd.request(event)
