"""The Discord side of who is voting, and why a ballot was refused.

Module-level so a button callback, which has an interaction and a client but no cog, can reach both.
"""

from typing import TYPE_CHECKING

import discord

from squid.bot.ui import tr
from squid.bot.utils.permissions import build_subject
from squid.permissions.domain.catalogue import (
    VOTE_LOG_DELETE_CAST,
    VOTE_POLL_CLOSE_ANY,
    VOTE_WEIGHT_STAFF,
)
from squid.voting.domain import VoteActor, VoteRejection

if TYPE_CHECKING:
    import squid.bot.app

REJECTION_MESSAGES = {
    VoteRejection.NOT_FOUND: tr(t"That message is not an open vote."),
    VoteRejection.CLOSED: tr(t"That vote is already closed."),
    VoteRejection.NOT_ELIGIBLE: tr(t"You do not have a trusted role."),
    VoteRejection.INVALID_OPTION: tr(t"That option is not available on this vote."),
    VoteRejection.WRONG_GUILD: tr(t"That vote belongs to a different server."),
    VoteRejection.NOT_AUTHORIZED: tr(t"Only the poll creator or staff can do that."),
}
"""One localizable sentence per rejection.

Keyed by the enum so a new domain rejection fails the lookup here instead of leaking its name into a channel.
"""


def describe_rejection(rejection: VoteRejection) -> str:
    """Render a typed rejection using the ambient localization."""
    return tr(REJECTION_MESSAGES[rejection])


async def resolve_actor(bot: squid.bot.app.RedstoneSquid, member: discord.Member, *, account_id: int) -> VoteActor:
    """Load every vote kind's nodes in one permission read, so the caller need not say which kind it asks about.

    `account_id` is required rather than minted here: a raw reaction must not create an account for someone
    never asked for consent, so callers establish consent first or refuse.
    """
    subject = await build_subject(bot, member, member.guild.id)
    capabilities = await bot.services.permissions.capabilities(
        subject,
        (VOTE_LOG_DELETE_CAST, VOTE_WEIGHT_STAFF, VOTE_POLL_CLOSE_ANY),
    )
    return VoteActor(
        account_id,
        member.id,
        member.guild.id,
        frozenset(role.id for role in member.roles),
        capabilities=capabilities,
    )
