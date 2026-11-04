"""Discord role and permission checks for portable action guards.

An access policy controls who may interact with the whole message root. These guards
control individual actions on an accessible panel, such as requiring a moderator role
only for its Delete button.
"""

from collections.abc import Awaitable, Callable
from collections.abc import Set as AbstractSet

import discord

from squid_ui.guards import Guard, permission
from squid_ui.interactions import ActionEvent
from squid_ui.text import TextLike
from squid_ui_discord.actions import _responder


def requires_role(role_id: int | AbstractSet[int], *, reason: TextLike | None = None) -> Guard:
    """Admit a member holding `role_id` — or any of them, given a set.

    A press from outside a guild is refused: there are no roles to hold there, and silently
    admitting would make the guard mean different things in different channels.
    """
    wanted = frozenset({role_id} if isinstance(role_id, int) else role_id)

    async def check(event: ActionEvent) -> bool:
        member = _responder(event).interaction.user
        if not isinstance(member, discord.Member):
            return False
        return any(role.id in wanted for role in member.roles)

    return permission(check, reason=reason)


def requires(check: Callable[[discord.Interaction], Awaitable[bool]], *, reason: TextLike | None = None) -> Guard:
    """Admit while an asynchronous check over the raw interaction passes.

    The escape hatch for facts no portable guard can name — channel permissions, a guild
    setting, an audit-log lookup.
    """

    async def portable(event: ActionEvent) -> bool:
        return await check(_responder(event).interaction)

    return permission(portable, reason=reason)
