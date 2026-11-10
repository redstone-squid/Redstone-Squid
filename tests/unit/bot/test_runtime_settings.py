"""Owner-only runtime edits resolve their audit actor through Squid accounts."""

import inspect
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from discord.ext import commands

from squid.accounts.domain import IdentityProvider
from squid.bot.admin import Admin
from squid.settings.domain.runtime import RuntimeSettings


@pytest.mark.parametrize("linked", [True, False])
async def test_runtime_edit_uses_internal_account_or_refuses_unlinked_owner(linked: bool) -> None:
    account = SimpleNamespace(id=17, needs_consent_refresh=False) if linked else None
    accounts = SimpleNamespace(get_account_by_identity=AsyncMock(return_value=account))
    settings = SimpleNamespace(update_runtime=AsyncMock(return_value=RuntimeSettings(welcome_forward_chance=0.25)))
    owner = SimpleNamespace(bot=SimpleNamespace(services=SimpleNamespace(accounts=accounts, settings=settings)))
    request = SimpleNamespace(user=SimpleNamespace(id=999999))

    callback = inspect.unwrap(cast(Any, Admin).runtime_settings.callback)
    await callback(owner, request, "welcome_forward_chance", value="0.25")

    accounts.get_account_by_identity.assert_awaited_once_with(IdentityProvider.DISCORD, "999999")
    if linked:
        settings.update_runtime.assert_awaited_once_with({"welcome_forward_chance": 0.25}, actor_account_id=17)
    else:
        settings.update_runtime.assert_not_awaited()


async def test_runtime_command_rejects_non_owner() -> None:
    context = SimpleNamespace(bot=SimpleNamespace(is_owner=AsyncMock(return_value=False)), author=object())
    assert len(cast(Any, Admin).runtime_settings.checks) == 1
    with pytest.raises(commands.NotOwner):
        await cast(Any, Admin).runtime_settings.checks[0](cast(Any, context))
