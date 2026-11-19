"""Retry message writes only when another authority can deliver them."""

from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import squid_ui as sl
from squid_ui.ext.discord.delivery import EditHandle, StaleHandleError, _MessageDelivery, handle_from
from squid_ui.ext.discord.messages.payload import MessagePayload
from squid_ui.ext.discord.testing import http_error, interaction_harness


@pytest.mark.parametrize("shared", [False, True])
async def test_stale_authority_is_attempted_once_before_fallback(shared: bool) -> None:
    stale = Mock(spec=EditHandle, expired=Mock(return_value=False), write=AsyncMock(side_effect=StaleHandleError()))
    fallback = stale if shared else Mock(spec=EditHandle, expired=Mock(return_value=False), write=AsyncMock())
    delivery = _MessageDelivery(handle=cast(EditHandle, fallback))
    payload = MessagePayload.classic(content="Updated")

    used = await delivery.write(payload, keep_attachments=True, through=cast(EditHandle, stale))

    stale.write.assert_awaited_once_with(payload, keep_attachments=True)
    if shared:
        assert used is None
        assert delivery.handle is None
    else:
        assert used is fallback
        assert delivery.handle is fallback
        fallback.write.assert_awaited_once_with(payload, keep_attachments=True)


async def test_other_write_errors_do_not_retry_through_standing_authority() -> None:
    error = RuntimeError("delivery failed")
    through = Mock(spec=EditHandle, expired=Mock(return_value=False), write=AsyncMock(side_effect=error))
    fallback = Mock(spec=EditHandle, expired=Mock(return_value=False), write=AsyncMock())
    delivery = _MessageDelivery(handle=cast(EditHandle, fallback))

    with pytest.raises(RuntimeError, match="delivery failed"):
        await delivery.write(MessagePayload.classic(content="Updated"), keep_attachments=False, through=through)

    fallback.write.assert_not_awaited()
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
