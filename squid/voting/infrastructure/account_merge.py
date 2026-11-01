"""Ballot conflict policy inside the account merge transaction."""

from sqlalchemy import Delete, and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from squid.persistence.account_merge import AccountMergeContext, AccountReference, move_account_references
from squid.voting.infrastructure.models import Vote

VOTING_REFERENCES = (AccountReference("vote_sessions", "author_account_id"),)


def _collapse_votes_statement(context: AccountMergeContext) -> Delete:
    absorbed = aliased(Vote, name="absorbed_vote")
    survivor = aliased(Vote, name="survivor_vote")
    duplicate_sessions = (
        select(absorbed.vote_session_id)
        .join(
            survivor,
            and_(
                survivor.account_id == context.survivor,
                survivor.vote_session_id == absorbed.vote_session_id,
            ),
        )
        .where(absorbed.account_id == context.absorbed)
    )
    return delete(Vote).where(
        Vote.account_id == context.absorbed,
        Vote.vote_session_id.in_(duplicate_sessions),
    )


async def merge_voting(session: AsyncSession, context: AccountMergeContext) -> None:
    """Move authored sessions and retain one ballot per merged voter."""
    await move_account_references(session, VOTING_REFERENCES, context)
    await session.execute(_collapse_votes_statement(context))
    await move_account_references(session, (AccountReference("votes", "account_id"),), context)
