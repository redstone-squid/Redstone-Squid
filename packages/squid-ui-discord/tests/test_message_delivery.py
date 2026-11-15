"""Retry message writes only when another authority can deliver them."""

from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from squid_ui.ext.discord.delivery import EditHandle, StaleHandleError
from squid_ui.ext.discord.message_delivery import _MessageDelivery
from squid_ui.ext.discord.message_payload import MessagePayload


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
