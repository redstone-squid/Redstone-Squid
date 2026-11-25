"""Screens and persistent actions occupy one production endpoint registry."""

from importlib import import_module
from typing import Any
from unittest.mock import AsyncMock

import pytest

from squid.bot import give_redstoner_cog as roles
from squid.bot.accounts import cog as accounts
from squid.bot.routes import AppRequest
from squid_ui.routing import InvalidAddress, MountedRouter
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services


@pytest.mark.parametrize(
    ("module", "name", "params", "address"),
    [
        ("squid.bot.voting.controls", "close_poll", {}, "/polls/close"),
        ("squid.bot.voting.controls", "refresh_poll", {}, "/polls/refresh"),
        ("squid.bot.submission.ui.controls", "edit_build", {"build_id": 5, "session": None}, "/builds/5/edit"),
        ("squid.bot.submission.consent_banner", "open_consent_prompt", {}, "/build-log-consents/new"),
        ("squid.bot.give_redstoner_cog", "remove_own_redstoner_role", {}, "/redstoner-roles/self/remove"),
    ],
)
def test_production_actions_are_canonical_addresses(
    module: str, name: str, params: dict[str, Any], address: str
) -> None:
    endpoint = getattr(import_module(module), name)
    assert endpoint.address(**params) == address
    assert endpoint.custom_id(**params) == "ui:" + address
    match = endpoint.router.resolve(address)
    assert match.endpoint is endpoint
    assert match.arguments == params


async def test_extension_setup_and_teardown_own_their_addresses() -> None:
    bot = make_layout_bot(add_cog=AsyncMock(), services=bot_services())
    router = MountedRouter[AppRequest]()
    bot.ui_router = router
    await accounts.setup(bot)
    await roles.setup(bot)
    address = roles.remove_own_redstoner_role.address()
    assert router.resolve(address).endpoint is roles.remove_own_redstoner_role
    await roles.teardown(bot)
    with pytest.raises(InvalidAddress):
        router.resolve(address)
    assert router.resolve("/account").endpoint is accounts.AccountWorkspace.open_account
    await roles.setup(bot)
    assert router.resolve(address).endpoint is roles.remove_own_redstoner_role
