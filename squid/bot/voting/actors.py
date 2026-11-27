"""Translate Discord members into vote actors and localize vote refusals."""

from dataclasses import replace
from typing import TYPE_CHECKING

import discord

from squid.bot.ui import tr
from squid.core.actors import UserActor
from squid.core.permissions.domain.catalogue import (
    VOTE_LOG_DELETE_CAST,
    VOTE_NETWORK_CLOSE_ANY,
    VOTE_NETWORK_RECOUNT,
    VOTE_POLL_CLOSE_ANY,
    VOTE_POLL_RECOUNT,
    VOTE_WEIGHT_STAFF,
)
from squid.core.voting.domain import VoteRejection
from squid_ui.content.text import Text

if TYPE_CHECKING:
    import squid.bot.app

REJECTION_MESSAGES = {
    VoteRejection.NOT_FOUND: tr(t"vote-message-not-open = That message is not an open vote."),
    VoteRejection.CLOSED: tr(t"bot-voting-actors-that-vote-is-already-closed = That vote is already closed."),
    VoteRejection.NOT_ELIGIBLE: tr(
        t"bot-voting-actors-you-do-not-have-a-trusted-role = You do not have a trusted role."
    ),
    VoteRejection.INVALID_OPTION: tr(t"vote-option-unavailable = That option is not available on this vote."),
    VoteRejection.WRONG_GUILD: tr(t"vote-server-mismatch = That vote belongs to a different server."),
    VoteRejection.NOT_AUTHORIZED: tr(
        t"vote-poll-action-permission-denied = Only the poll creator or staff can do that."
    ),
}
"""One localizable sentence per typed rejection.

Keyed by the enum rather than formatted from it, so adding a rejection to the domain
fails the lookup here instead of leaking `not_eligible` into a user's channel.
"""


def describe_rejection(rejection: VoteRejection) -> Text:
    """Return the sentence that explains a typed rejection."""
    return REJECTION_MESSAGES[rejection]


async def resolve_actor(
    bot: squid.bot.app.RedstoneSquid, member: discord.Member, *, account_id: int | None
) -> UserActor:
    """Resolve the member's vote capabilities in one permission query.

    The caller supplies an account ID when the voter has one; None
    makes the member vote under their Discord ID. This function loads permissions
    and uses roles from the supplied member object; it does not create an account
    or refresh Discord membership.
    """
    subject = replace(await bot.actors.resolve(member, account_id=account_id), account_id=account_id)
    capabilities = (
        await bot.services.permissions.check(
            subject,
            *(
                VOTE_LOG_DELETE_CAST,
                VOTE_WEIGHT_STAFF,
                VOTE_POLL_CLOSE_ANY,
                VOTE_POLL_RECOUNT,
                VOTE_NETWORK_CLOSE_ANY,
                VOTE_NETWORK_RECOUNT,
            ),
        )
    ).allowed_nodes
    return replace(subject, capabilities=capabilities)
