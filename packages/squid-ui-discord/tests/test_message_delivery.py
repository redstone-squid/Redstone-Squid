"""Retry message writes only when another authority can deliver them."""

from typing import Any

import discord
import pytest

import squid_ui as sl
from squid_ui.ext.discord.delivery import _MessageDelivery, handle_for, handle_for_original, handle_from
from squid_ui.ext.discord.messages.payload import MessageMode, MessagePayload
from squid_ui.ext.discord.testing import http_error, interaction_harness, message_harness, stale_http_error


@pytest.mark.parametrize("shared", [False, True])
async def test_stale_authority_is_attempted_once_before_fallback(shared: bool) -> None:
    interaction = interaction_harness(components_v2=False)
    interaction.edit_original_response.error = stale_http_error()
    message = message_harness(components_v2=False)
    stale = handle_for_original(interaction, mode=MessageMode.CLASSIC)
    fallback = stale if shared else handle_for(message)
    delivery = _MessageDelivery(handle=fallback)
    payload = MessagePayload.classic(content="Updated")

    used = await delivery.write(payload, keep_attachments=True, through=stale)

    (attempt,) = interaction.edit_original_response.records
    assert attempt.kwargs["content"] == "Updated"
    assert "attachments" not in attempt.kwargs
    if shared:
        assert used is None
        assert delivery.handle is None
        message.edit.assert_not_awaited()
    else:
        assert used is delivery.handle is fallback
        (retry,) = message.edit.records
        assert retry.kwargs["content"] == "Updated"
        assert "attachments" not in retry.kwargs


async def test_other_write_errors_do_not_retry_through_standing_authority() -> None:
    error = http_error(500, message="delivery failed")
    interaction = interaction_harness(components_v2=False)
    interaction.edit_original_response.error = error
    message = message_harness()
    through = handle_for_original(interaction, mode=MessageMode.CLASSIC)
    fallback = handle_for(message)
    delivery = _MessageDelivery(handle=fallback)

    with pytest.raises(discord.HTTPException) as raised:
        await delivery.write(MessagePayload.classic(content="Updated"), keep_attachments=False, through=through)

    assert raised.value is error
    message.edit.assert_not_awaited()
    assert delivery.handle is fallback


@pytest.mark.parametrize("remote", [False, True])
async def test_edit_race_rewinds_uploaded_files_before_webhook_retry(remote: bool) -> None:
    interaction = interaction_harness(components_v2=False)
    payload = MessagePayload.classic(assets=(sl.file(b"schematic", filename="build.schem"),))
    uploads: list[bytes] = []

    async def lose_response(**fields: Any) -> None:
        uploads.append(fields["attachments"][0].fp.read())
        if remote:
            raise http_error(400, code=40060)
        raise discord.InteractionResponded(interaction)

    async def retry(message_id: int, **fields: Any) -> None:
        assert message_id == interaction.message.id
        uploads.append(fields["attachments"][0].fp.read())

    interaction.response.edit_message.callback = lose_response
    interaction.followup.edit_message.callback = retry
    handle = handle_from(interaction)
    assert handle is not None

    await handle.write(payload)

    assert uploads == [b"schematic", b"schematic"]
