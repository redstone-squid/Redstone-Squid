"""Localized invocation rendering, delivery, mounting, and session opening."""

from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui.text import Localization, Message
from squid_ui_discord.invocation import Invocation, Private, current_invocation, invocation_scope
from squid_ui_discord.sessions import AdmissionSpec, Opened, Reject, Rejected
from squid_ui_discord.testing import interaction_harness, message_harness


class FakeClient:
    """A weak-referenceable installed client double."""


class Panel(sl.Component[sl.ComponentsV2Target]):
    def render(self):
        return [sl.heading("Panel")]


def _context(
    client: FakeClient,
    *,
    guild: bool = True,
    interaction: Any | None = None,
    dm: AsyncMock | None = None,
) -> Any:
    author = SimpleNamespace(id=7, send=dm or AsyncMock(return_value=message_harness(guild_id=None)))
    return SimpleNamespace(
        bot=client,
        author=author,
        guild=SimpleNamespace(id=42) if guild else None,
        interaction=interaction,
        send=AsyncMock(return_value=message_harness()),
    )


def _interaction(client: FakeClient) -> Any:
    interaction = interaction_harness(user_id=7)
    interaction.client = client
    interaction.guild = SimpleNamespace(id=7)
    return interaction


def _message(client: FakeClient, *, guild: bool = True) -> Any:
    message = message_harness(guild_id=42 if guild else None)
    message.client = client
    message.author = SimpleNamespace(id=7, send=AsyncMock(return_value=message_harness(guild_id=None)))
    message.channel.send = AsyncMock(return_value=message_harness())
    return message


def test_invocation_has_one_async_construction_path() -> None:
    with pytest.raises(TypeError, match=r"await Invocation\.of"):
        Invocation()


async def test_of_resolves_the_hook_once_inside_an_ambient_scope() -> None:
    client = FakeClient()
    calls: list[object] = []

    async def resolve(source: sd.InvocationSource) -> Localization:
        calls.append(source)
        return Localization(locale="pirate", gettext=lambda message: f"arrr {message}")

    sd.install(cast(discord.Client, client), localization=resolve)
    context = _context(client)

    assert current_invocation() is None
    with invocation_scope(context):
        assert current_invocation() is None
        first = await Invocation.of(context)
        second = await Invocation.of(context)
        assert current_invocation() is first

    assert first is second
    assert first.client is client
    assert first.user is context.author
    assert first.guild is context.guild
    assert calls == [context]
    assert current_invocation() is None


async def test_of_uses_installed_defaults_without_a_resolver() -> None:
    client = FakeClient()
    localization = Localization(locale="en-GB")
    sd.install(
        cast(discord.Client, client),
        defaults=sd.MessageRootDefaults(localization=localization),
    )

    invocation = await Invocation.of(_context(client))

    assert invocation.localization is localization


@pytest.mark.parametrize(
    ("source_kind", "visibility", "ephemeral"),
    [
        ("context", "public", False),
        ("context", "personal", False),
        ("slash_context", "public", False),
        ("slash_context", "personal", True),
        ("interaction", "public", False),
        ("interaction", "personal", True),
    ],
)
async def test_destination_maps_command_and_interaction_visibility(
    source_kind: str, visibility: str, ephemeral: bool
) -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    interaction = _interaction(client)
    if source_kind == "context":
        source = _context(client)
        send = source.send
    elif source_kind == "slash_context":
        source = _context(client, interaction=interaction)
        send = source.send
    else:
        source = interaction
        send = interaction.response.send_message

    invocation = await Invocation.of(source)
    await invocation.reply(sl.paragraph("hello"), visibility=cast(Any, visibility))

    assert send.await_args.kwargs["ephemeral"] is ephemeral


@pytest.mark.parametrize("visibility", ["public", "personal"])
async def test_message_destination_sends_to_the_message_channel(visibility: str) -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    message = _message(client)

    invocation = await Invocation.of(message)
    await invocation.reply(sl.paragraph("hello"), visibility=cast(Any, visibility))

    message.channel.send.assert_awaited_once()
    assert "ephemeral" not in message.channel.send.await_args.kwargs


async def test_destination_passes_host_files_to_an_interaction_response() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    interaction = _interaction(client)
    attachment = discord.File(BytesIO(b"proof"), filename="proof.txt")

    invocation = await Invocation.of(interaction)
    await invocation.reply(sl.paragraph("hello"), files=(attachment,))

    assert interaction.response.send_message.await_args.kwargs["files"] == [attachment]


async def test_private_prefix_delivery_uses_dm_and_confirms_in_the_channel() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    context = _context(client)

    invocation = await Invocation.of(context)
    result = await invocation.reply(sl.paragraph("secret"), visibility=Private("account recovery code"))

    assert result.message is not None
    context.author.send.assert_awaited_once()
    context.send.assert_awaited_once()


async def test_private_prefix_delivery_reports_closed_dms_without_leaking_the_payload() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    response = SimpleNamespace(status=403, reason="Forbidden", headers={})
    forbidden = discord.Forbidden(cast(Any, response), {"code": 50007, "message": "Cannot send messages to this user"})
    context = _context(client, dm=AsyncMock(side_effect=forbidden))

    invocation = await Invocation.of(context)
    with pytest.raises(sd.delivery.DeliveryAbandoned):
        await invocation.reply(sl.paragraph("secret"), visibility=Private("account recovery code"))

    context.author.send.assert_awaited_once()
    context.send.assert_awaited_once()


async def test_private_interaction_delivery_is_ephemeral() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    interaction = _interaction(client)

    invocation = await Invocation.of(interaction)
    await invocation.reply(sl.paragraph("secret"), visibility=Private("account recovery code"))

    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_mount_uses_runtime_defaults_and_invocation_localization() -> None:
    client = FakeClient()
    localization = Localization(locale="fr")

    async def resolve(source: sd.InvocationSource) -> Localization:
        del source
        return localization

    sd.install(
        cast(discord.Client, client),
        defaults=sd.MessageRootDefaults(timeout=30),
        localization=resolve,
    )
    context = _context(client)
    invocation = await Invocation.of(context)

    message_root = await invocation.mount(Panel(), access=sd.Owner(7), timeout=None)

    assert message_root.localization is localization
    assert message_root.timeout is None
    assert message_root.access == sd.Owner(7)
    context.send.assert_awaited_once()


async def test_open_can_override_the_spec_key() -> None:
    client = FakeClient()
    runtime = sd.install(cast(discord.Client, client))
    context = _context(client)
    invocation = await Invocation.of(context)
    key = sd.SessionKey.custom("build-edit", (7, 99))

    result = await invocation.open(Panel(), sd.SessionSpec("panel"), key=key)

    assert isinstance(result, Opened)
    assert result.session.key == key
    assert runtime.sessions.get(key) == (result.session,)


async def test_open_renders_a_rejection_notice_before_returning() -> None:
    client = FakeClient()
    sd.install(cast(discord.Client, client))
    context = _context(client)
    invocation = await Invocation.of(context)
    notice = Message("This panel is already open.")
    spec = sd.SessionSpec("panel", admission=AdmissionSpec(collision=Reject(notice=notice)))

    first = await invocation.open(Panel(), spec)
    result = await invocation.open(Panel(), spec)

    assert isinstance(first, Opened)
    assert isinstance(result, Rejected)
    assert result.notice is notice
    assert context.send.await_count == 2
