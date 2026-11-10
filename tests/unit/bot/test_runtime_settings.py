"""Permission-gated runtime edits resolve their audit actor through Squid accounts."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from squid.accounts.domain import IdentityProvider
from squid.bot.admin import Admin
from squid.core.errors import AuthorizationError
from squid.permissions.domain.catalogue import SETTINGS_RUNTIME_EDIT, SETTINGS_RUNTIME_VIEW
from squid.settings.domain.runtime import RuntimeSettings


@pytest.mark.parametrize("linked", [True, False])
async def test_runtime_edit_uses_internal_account_or_refuses_unlinked_owner(
    linked: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorize = AsyncMock()
    monkeypatch.setattr("squid.bot.admin.enforce", authorize)
    account = SimpleNamespace(id=17, needs_consent_refresh=False) if linked else None
    accounts = SimpleNamespace(get_account_by_identity=AsyncMock(return_value=account))
    settings = SimpleNamespace(update_runtime=AsyncMock(return_value=RuntimeSettings(welcome_forward_chance=0.25)))
    owner = SimpleNamespace(bot=SimpleNamespace(services=SimpleNamespace(accounts=accounts, settings=settings)))
    request = SimpleNamespace(user=SimpleNamespace(id=999999))

    callback = cast(Any, Admin)._runtime_settings
    await callback(owner, request, "welcome_forward_chance", value="0.25")

    authorize.assert_awaited_once_with(request, SETTINGS_RUNTIME_EDIT)
    accounts.get_account_by_identity.assert_awaited_once_with(IdentityProvider.DISCORD, "999999")
    if linked:
        settings.update_runtime.assert_awaited_once_with({"welcome_forward_chance": 0.25}, actor_account_id=17)
    else:
        settings.update_runtime.assert_not_awaited()


@pytest.mark.parametrize(("value", "node"), [(None, SETTINGS_RUNTIME_VIEW), ("false", SETTINGS_RUNTIME_EDIT)])
async def test_runtime_command_checks_permission_before_reading_settings(
    value: str | None,
    node: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorize = AsyncMock(side_effect=AuthorizationError)
    monkeypatch.setattr("squid.bot.admin.enforce", authorize)
    callback = cast(Any, Admin)._runtime_settings
    request = SimpleNamespace()
    with pytest.raises(AuthorizationError):
        await callback(SimpleNamespace(), request, "welcome_enabled", value=value)
    authorize.assert_awaited_once_with(request, node)
