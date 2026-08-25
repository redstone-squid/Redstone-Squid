"""Discord-layer admission sugar over the portable guard vocabulary.

Roles are a Discord fact, so `requires_role` cannot live in the portable core the way
`cooldown` or `until` can. It is `sl.guards.permission` with the lookup written once.

Its relation to an access policy: `squid_discord.Check` gates the whole mount, this gates one
action on an otherwise reachable panel.
"""

from collections.abc import Awaitable, Callable
from collections.abc import Set as AbstractSet

import discord

from squid_discord.actions import native
from squid_layouts.guards import Guard, permission
from squid_layouts.interactions import ActionEvent
from squid_layouts.text import TextLike


def requires_role(role_id: int | AbstractSet[int], *, reason: TextLike | None = None) -> Guard:
    """Admit a member holding `role_id` — or any of them, given a set.

    A press from outside a guild is refused: there are no roles to hold there, and silently
    admitting would make the guard mean different things in different channels.
    """
    wanted = frozenset({role_id} if isinstance(role_id, int) else role_id)

    async def check(event: ActionEvent) -> bool:
        member = native(event).user
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
        return await check(native(event))

    return permission(portable, reason=reason)
