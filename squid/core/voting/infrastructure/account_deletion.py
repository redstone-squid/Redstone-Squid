"""Votes that block an account deletion.

Ballots and closed sessions stay with the tombstone account and keep counting.
A session the account opened must close first, since closing a poll is its author's job.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.voting.domain import VoteStatus
from squid.core.voting.models import VoteSession


async def count_open_vote_sessions(session: AsyncSession, account_id: int) -> int:
    """Count sessions the account opened that are still collecting or counting ballots."""
    return (
        await session.scalar(
            select(func.count()).where(
                VoteSession.author_account_id == account_id,
                VoteSession.status.in_((VoteStatus.OPEN, VoteStatus.COUNTING, VoteStatus.REVIEW)),
            )
        )
        or 0
    )
