"""SQLAlchemy persistence for atomic vote mutations."""

from collections.abc import Mapping

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.schema import (
    BuildVoteSession,
    DeleteLogVoteSession,
    Message,
    Vote,
    VoteSession,
    VoteSessionResultLiteral,
)
from squid.services.votes import StoredVoteMutation, VoteSessionSnapshot, VoteTarget


class SQLAlchemyVoteRepository:
    """Store votes while serializing mutation and closure per session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None:
        async with self._session_factory() as session:
            row = await self._get_session_row(session, message_id)
            if row is None:
                return None
            votes = await self._get_votes(session, row.id)
            return await self._to_snapshot(session, row, votes)

    async def cast_vote(
        self,
        message_id: int,
        user_id: int,
        desired_weight: float,
    ) -> StoredVoteMutation | None:
        async with self._session_factory() as session, session.begin():
            row = await self._get_session_row(session, message_id, for_update=True)
            if row is None or row.status != "open":
                return None

            votes = await self._get_votes(session, row.id)
            previous_weight = votes.get(user_id)
            current_weight = None if previous_weight == desired_weight else desired_weight
            if current_weight is None:
                await session.execute(delete(Vote).where(Vote.vote_session_id == row.id, Vote.user_id == user_id))
                votes.pop(user_id, None)
            else:
                await session.execute(
                    pg_insert(Vote)
                    .values(vote_session_id=row.id, user_id=user_id, weight=current_weight)
                    .on_conflict_do_update(
                        index_elements=[Vote.vote_session_id, Vote.user_id],
                        set_={"weight": current_weight},
                    )
                )
                votes[user_id] = current_weight

            net_votes = sum(votes.values())
            result: VoteSessionResultLiteral = "pending"
            just_closed = False
            if net_votes >= row.pass_threshold:
                result = "approved"
                just_closed = True
            elif net_votes <= row.fail_threshold:
                result = "denied"
                just_closed = True

            if just_closed:
                await session.execute(
                    update(VoteSession)
                    .where(VoteSession.id == row.id, VoteSession.status == "open")
                    .values(status="closed", result=result)
                )
                row.status = "closed"
                row.result = result

            snapshot = await self._to_snapshot(session, row, votes)
            return StoredVoteMutation(
                session=snapshot,
                previous_weight=previous_weight,
                current_weight=current_weight,
                just_closed=just_closed,
            )

    @staticmethod
    async def _get_session_row(
        session: AsyncSession,
        message_id: int,
        *,
        for_update: bool = False,
    ) -> VoteSession | None:
        stmt = (
            select(VoteSession)
            .join(Message, Message.vote_session_id == VoteSession.id)
            .where(Message.id == message_id, Message.purpose == "vote")
        )
        if for_update:
            stmt = stmt.with_for_update(of=VoteSession)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_votes(session: AsyncSession, vote_session_id: int) -> dict[int, float]:
        result = await session.execute(select(Vote.user_id, Vote.weight).where(Vote.vote_session_id == vote_session_id))
        return dict(result.tuples().all())

    @staticmethod
    async def _to_snapshot(
        session: AsyncSession,
        row: VoteSession,
        votes: Mapping[int, float],
    ) -> VoteSessionSnapshot:
        message_result = await session.execute(
            select(Message.id).where(Message.vote_session_id == row.id).order_by(Message.id)
        )
        message_ids = tuple(message_result.scalars().all())
        target = VoteTarget()
        kind = row.kind
        if kind == "build":
            result = await session.execute(
                select(BuildVoteSession.build_id).where(BuildVoteSession.vote_session_id == row.id)
            )
            target = VoteTarget(build_id=result.scalar_one_or_none())
        elif kind == "delete_log":
            result = await session.execute(
                select(
                    DeleteLogVoteSession.target_message_id,
                    DeleteLogVoteSession.target_channel_id,
                    DeleteLogVoteSession.target_server_id,
                ).where(DeleteLogVoteSession.vote_session_id == row.id)
            )
            target_row = result.tuples().one_or_none()
            if target_row is not None:
                target = VoteTarget(
                    message_id=target_row[0],
                    channel_id=target_row[1],
                    server_id=target_row[2],
                )

        return VoteSessionSnapshot(
            id=row.id,
            kind=kind,  # pyright: ignore[reportArgumentType]
            status=row.status,  # pyright: ignore[reportArgumentType]
            result=row.result,
            pass_threshold=row.pass_threshold,
            fail_threshold=row.fail_threshold,
            votes=dict(votes),
            message_ids=message_ids,
            target=target,
        )
