"""Translate Discord members into vote actors and localize vote refusals."""

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
"""One localizable sentence per typed rejection.

Keyed by the enum rather than formatted from it, so adding a rejection to the domain
fails the lookup here instead of leaking `not_eligible` into a user's channel.
"""


def describe_rejection(rejection: VoteRejection) -> str:
    """Render a typed rejection using the ambient localization."""
    return tr(REJECTION_MESSAGES[rejection])


async def resolve_actor(bot: squid.bot.app.RedstoneSquid, member: discord.Member, *, account_id: int) -> VoteActor:
    """Resolve the member's vote capabilities in one permission query.

    The caller supplies an account ID after checking consent when needed. This
    function loads permissions and uses roles from the supplied member object;
    it does not create an account or refresh Discord membership.
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
