"""Resolve the acting Discord account for user-initiated work."""

from collections.abc import Awaitable, Callable
from typing import Any

import squid_ui.ext.discord as sd
from squid.core.accounts.application import AccountService
from squid.core.accounts.domain import IdentityProvider
from squid.core.actors import Actor, UserActor


async def with_account(
    event: sd.ActionEvent,
    accounts: AccountService,
    work: Callable[[sd.ActionEvent, int], Awaitable[None]],
) -> None:
    account_id = await ensure_account(event.context, accounts)
    await work(event, account_id)


async def ensure_account(
    request: sd.Request[Any, Any],
    accounts: AccountService,
    *,
    resolve_actor: Callable[[], Awaitable[Actor]] | None = None,
) -> int:
    """Resolve the acting Discord user's account for a requested operation."""
    actor = (await resolve_actor()).require_user() if resolve_actor else UserActor(discord_user_id=request.user.id)
    with actor.audit_scope():
        account = await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(actor.require_discord_user_id()))
    return account.id
