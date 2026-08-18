"""The Discord side of who is voting, and why a ballot was refused.

Both halves used to live on `VoteCog`, which made them unreachable from a button on a
vote card: a component callback has an interaction and a client, not a cog.
"""

from typing import TYPE_CHECKING

import discord

from squid.bot.i18n import t
from squid.bot.utils.permissions import build_subject
from squid.core.i18n import _
from squid.permissions.domain.catalogue import (
    VOTE_LOG_DELETE_CAST,
    VOTE_POLL_CLOSE_ANY,
    VOTE_WEIGHT_STAFF,
)
from squid.voting.domain import VoteActor, VoteRejection

if TYPE_CHECKING:
    import squid.bot.app

REJECTION_MESSAGES = {
    VoteRejection.NOT_FOUND: _("That message is not an open vote."),
    VoteRejection.CLOSED: _("That vote is already closed."),
    VoteRejection.NOT_ELIGIBLE: _("You do not have a trusted role."),
    VoteRejection.INVALID_OPTION: _("That option is not available on this vote."),
    VoteRejection.WRONG_GUILD: _("That vote belongs to a different server."),
    VoteRejection.NOT_AUTHORIZED: _("Only the poll creator or staff can do that."),
}
"""One localizable sentence per typed rejection.

Keyed by the enum rather than formatted from it, so adding a rejection to the domain
fails the lookup here instead of leaking `not_eligible` into a user's channel.
"""


def describe_rejection(locale: str | None, rejection: VoteRejection) -> str:
    """Render a typed rejection as a localized sentence."""
    return t(locale, REJECTION_MESSAGES[rejection])


async def resolve_actor(bot: squid.bot.app.RedstoneSquid, member: discord.Member, *, account_id: int) -> VoteActor:
    """Resolve one member's vote capabilities in a single permission load.

    The tiers this replaces cost up to four round trips here -- a global admin lookup, a
    guild lookup and a settings read, twice over. Every kind's nodes are loaded together,
    so the caller does not say which kind it is asking about.

    The account id is required rather than resolved here. This used to mint one on sight,
    which meant a raw reaction wrote a row naming somebody who had never been asked; every
    caller now establishes consent first, and the ones that cannot ask refuse instead.
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
