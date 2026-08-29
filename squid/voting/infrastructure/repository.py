"""SQLAlchemy voting repository."""

from collections.abc import Sequence

from sqlalchemy import Text, delete, func, insert, or_, select, update
from sqlalchemy import cast as cast_sql
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.messages.infrastructure.models import Message
from squid.persistence.repository import BaseAsyncRepository
from squid.posts.infrastructure.models import DiscordPost
from squid.voting.domain import (
    DEFAULT_VOTE_OPTIONS,
    BuildVoteTarget,
    DeleteLogVoteTarget,
    EmojiPreset,
    GenericPoll,
    PollScope,
    RoleWeight,
    StoredVoteMutation,
    VoteChange,
    VoteChoice,
    VoteKind,
    VoteMessage,
    VoteOption,
    VoteSelection,
    VoteSessionResult,
    VoteSessionSnapshot,
    VoteStatus,
    VoteTarget,
    VoteVisibility,
    normalize_vote_options,
)
from squid.voting.infrastructure.models import (
    BuildVoteSession,
    DeleteLogVoteSession,
    GenericVoteSession,
    GuildVoteEmoji,
    GuildVoteRoleWeight,
    Vote,
    VoteSession,
    VoteSessionOption,
)


class _VoteSessionModelRepository(BaseAsyncRepository[VoteSession]):
    model_type = VoteSession


class VoteRepository:
    """Store selections while serializing refresh, mutation, and closure per session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def get_or_create_build_submission_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> int:
        """Serialize initial-review creation and return the existing session on retry."""
        options = normalize_vote_options(options, kind=VoteKind.BUILD)
        async with self._session_factory.begin() as session:
            lock_key = f"build-submission-vote:{build_id}"
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))
            existing = await session.scalar(
                select(BuildVoteSession.vote_session_id)
                .join(VoteSession, VoteSession.id == BuildVoteSession.vote_session_id)
                .where(BuildVoteSession.build_id == build_id, VoteSession.kind == VoteKind.BUILD)
                .order_by(BuildVoteSession.vote_session_id)
                .limit(1)
            )
            if existing is not None:
                return existing
            session_id = await self._create_session(
                session, author_account_id, VoteKind.BUILD, pass_threshold, fail_threshold, options
            )
            await session.execute(
                insert(BuildVoteSession).values(vote_session_id=session_id, build_id=build_id, changes=list(changes))
            )
            return session_id

    async def create_build_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> int:
        options = normalize_vote_options(options, kind=VoteKind.BUILD)
        async with self._session_factory.begin() as session:
            session_id = await self._create_session(
                session, author_account_id, VoteKind.BUILD, pass_threshold, fail_threshold, options
            )
            await session.execute(
                insert(BuildVoteSession).values(vote_session_id=session_id, build_id=build_id, changes=list(changes))
            )
            return session_id

    async def create_delete_log_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        message_id: int,
        channel_id: int,
        server_id: int,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> int:
        options = normalize_vote_options(options, kind=VoteKind.DELETE_LOG)
        async with self._session_factory.begin() as session:
            session_id = await self._create_session(
                session, author_account_id, VoteKind.DELETE_LOG, pass_threshold, fail_threshold, options
            )
            await session.execute(
                insert(DeleteLogVoteSession).values(
                    vote_session_id=session_id,
                    target_message_id=message_id,
                    target_channel_id=channel_id,
                    target_server_id=server_id,
                )
            )
            return session_id

    async def create_generic_session(
        self,
        *,
        author_account_id: int,
        question: str,
        visibility: VoteVisibility,
        deadline: Instant,
        options: Sequence[VoteOption],
        guild_id: int | None = None,
        scope: PollScope = PollScope.GUILD,
    ) -> int:
        options = normalize_vote_options(options, kind=VoteKind.GENERIC)
        async with self._session_factory.begin() as session:
            session_id = await self._create_session(session, author_account_id, VoteKind.GENERIC, None, None, options)
            await session.execute(
                insert(GenericVoteSession).values(
                    vote_session_id=session_id,
                    guild_id=guild_id,
                    question=question,
                    visibility=visibility,
                    scope=scope,
                    deadline=deadline,
                )
            )
            return session_id

    @staticmethod
    async def _create_session(
        session: AsyncSession,
        author_account_id: int,
        kind: VoteKind,
        pass_threshold: int | None,
        fail_threshold: int | None,
        options: Sequence[VoteOption],
    ) -> int:
        session_id = (
            await session.execute(
                insert(VoteSession)
                .values(
                    status=VoteStatus.OPEN,
                    result=VoteSessionResult.PENDING,
                    author_account_id=author_account_id,
                    kind=kind,
                    pass_threshold=pass_threshold,
                    fail_threshold=fail_threshold,
                )
                .returning(VoteSession.id)
            )
        ).scalar_one()
        await session.execute(
            insert(VoteSessionOption),
            [
                {
                    "vote_session_id": session_id,
                    "identifier": option.identifier,
                    "guild_id": option.guild_id or 0,
                    "emoji": option.emoji,
                    "choice": option.choice.value,
                    "label": option.label,
                    "multiplier": option.multiplier,
                    "position": position,
                }
                for position, option in enumerate(options)
            ],
        )
        return session_id

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None:
        async with self._session_factory() as session:
            row = await self._get_session_row(session, message_id)
            return None if row is None else await self._to_snapshot(session, row)

    async def get_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        async with self._session_factory() as session:
            row = await _VoteSessionModelRepository(session=session).get_one_or_none(id=vote_session_id)
            return None if row is None else await self._to_snapshot(session, row)

    async def list_open(self, kind: VoteKind) -> Sequence[VoteSessionSnapshot]:
        async with self._session_factory() as session:
            rows = await _VoteSessionModelRepository(session=session).get_many(
                VoteSession.status == VoteStatus.OPEN, VoteSession.kind == kind
            )
            return [await self._to_snapshot(session, row) for row in rows]

    async def list_due(self, now: Instant) -> Sequence[VoteSessionSnapshot]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(VoteSession)
                    .join(GenericVoteSession, GenericVoteSession.vote_session_id == VoteSession.id)
                    .where(VoteSession.status == VoteStatus.OPEN, GenericVoteSession.deadline <= now)
                )
            ).all()
            return [await self._to_snapshot(session, row) for row in rows]

    async def cast_vote(
        self,
        message_id: int,
        account_id: int,
        guild_id: int,
        option_id: str,
        emoji: str,
        desired_weight: float,
        refreshed_weights: dict[int, float] | None = None,
    ) -> StoredVoteMutation | None:
        async with self._session_factory() as session, session.begin():
            row = await self._get_session_row(session, message_id, for_update=True)
            if row is None or row.status is not VoteStatus.OPEN:
                return None
            await self._apply_refresh(session, row.id, refreshed_weights or {})
            previous = await session.scalar(
                select(Vote).where(Vote.vote_session_id == row.id, Vote.account_id == account_id)
            )
            previous_weight = previous.weight if previous is not None else None
            toggled_off = previous is not None and previous.option_id == option_id
            if toggled_off:
                await session.delete(previous)
                current_weight = None
            else:
                await session.execute(
                    pg_insert(Vote)
                    .values(
                        vote_session_id=row.id,
                        account_id=account_id,
                        guild_id=guild_id,
                        option_id=option_id,
                        emoji=emoji,
                        weight=desired_weight,
                    )
                    .on_conflict_do_update(
                        index_elements=[Vote.vote_session_id, Vote.account_id],
                        set_={
                            "guild_id": guild_id,
                            "option_id": option_id,
                            "emoji": emoji,
                            "weight": desired_weight,
                        },
                    )
                )
                current_weight = desired_weight
            just_closed = await self._close_at_threshold(session, row)
            return StoredVoteMutation(
                await self._to_snapshot(session, row), previous_weight, current_weight, just_closed
            )

    async def refresh_weights(self, vote_session_id: int, weights: dict[int, float]) -> StoredVoteMutation | None:
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(select(VoteSession).where(VoteSession.id == vote_session_id).with_for_update())
            if row is None:
                return None
            await self._apply_refresh(session, row.id, weights)
            just_closed = row.status is VoteStatus.OPEN and await self._close_at_threshold(session, row)
            return StoredVoteMutation(await self._to_snapshot(session, row), None, None, just_closed)

    async def close(self, message_id: int) -> StoredVoteMutation | None:
        async with self._session_factory() as session, session.begin():
            row = await self._get_session_row(session, message_id, for_update=True)
            return await self._close_row(session, row)

    async def close_by_id(self, vote_session_id: int) -> StoredVoteMutation | None:
        """Close a session by aggregate ID for the deadline scheduler."""
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(select(VoteSession).where(VoteSession.id == vote_session_id).with_for_update())
            return await self._close_row(session, row)

    @staticmethod
    async def _close_row(session: AsyncSession, row: VoteSession | None) -> StoredVoteMutation | None:
        if row is None or row.status is not VoteStatus.OPEN:
            return None
        row.status = VoteStatus.CLOSED
        row.result = VoteSessionResult.CANCELLED
        return StoredVoteMutation(
            session=await VoteRepository._to_snapshot(session, row),
            previous_weight=None,
            current_weight=None,
            just_closed=True,
        )

    @staticmethod
    async def _apply_refresh(session: AsyncSession, session_id: int, weights: dict[int, float]) -> None:
        for account_id, weight in weights.items():
            if weight <= 0:
                await session.execute(
                    delete(Vote).where(Vote.vote_session_id == session_id, Vote.account_id == account_id)
                )
            else:
                await session.execute(
                    update(Vote)
                    .where(Vote.vote_session_id == session_id, Vote.account_id == account_id)
                    .values(weight=weight)
                )

    @staticmethod
    async def _close_at_threshold(session: AsyncSession, row: VoteSession) -> bool:
        if row.pass_threshold is None or row.fail_threshold is None:
            return False
        selections = (
            await session.execute(
                select(Vote.weight, VoteSessionOption.choice)
                .join(
                    VoteSessionOption,
                    (VoteSessionOption.vote_session_id == Vote.vote_session_id)
                    & (VoteSessionOption.identifier == Vote.option_id)
                    & (VoteSessionOption.emoji == Vote.emoji)
                    & or_(VoteSessionOption.guild_id == Vote.guild_id, VoteSessionOption.guild_id == 0),
                )
                .where(Vote.vote_session_id == row.id)
            )
        ).tuples()
        net = sum(weight if choice is VoteChoice.APPROVE else -weight for weight, choice in selections)
        if net >= row.pass_threshold:
            result = VoteSessionResult.APPROVED
        elif net <= row.fail_threshold:
            result = VoteSessionResult.DENIED
        else:
            return False
        row.status = VoteStatus.CLOSED
        row.result = result
        return True

    async def get_emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset | None:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(GuildVoteEmoji)
                    .where(GuildVoteEmoji.guild_id == guild_id, GuildVoteEmoji.kind == kind)
                    .order_by(GuildVoteEmoji.position)
                )
            ).all()
            if not rows:
                return None
            return EmojiPreset(
                guild_id,
                kind,
                tuple(
                    VoteOption(
                        row.emoji,
                        row.choice,
                        identifier=row.identifier,
                        guild_id=guild_id,
                        label=row.label,
                        position=row.position,
                    )
                    for row in rows
                ),
            )

    async def set_emoji_preset(self, preset: EmojiPreset) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                delete(GuildVoteEmoji).where(
                    GuildVoteEmoji.guild_id == preset.guild_id, GuildVoteEmoji.kind == preset.kind
                )
            )
            await session.execute(
                insert(GuildVoteEmoji),
                [
                    {
                        "guild_id": preset.guild_id,
                        "kind": preset.kind.value,
                        "identifier": option.identifier,
                        "emoji": option.emoji,
                        "choice": option.choice.value,
                        "label": option.label,
                        "position": position,
                    }
                    for position, option in enumerate(preset.options)
                ],
            )

    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> Sequence[RoleWeight]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(GuildVoteRoleWeight).where(
                        GuildVoteRoleWeight.guild_id == guild_id, GuildVoteRoleWeight.kind == kind
                    )
                )
            ).all()
            return [RoleWeight(row.guild_id, row.kind, row.role_id, row.multiplier) for row in rows]

    async def set_role_weight(self, weight: RoleWeight) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                pg_insert(GuildVoteRoleWeight)
                .values(
                    guild_id=weight.guild_id,
                    kind=weight.kind.value,
                    role_id=weight.role_id,
                    multiplier=weight.multiplier,
                )
                .on_conflict_do_update(
                    index_elements=[
                        GuildVoteRoleWeight.guild_id,
                        GuildVoteRoleWeight.kind,
                        GuildVoteRoleWeight.role_id,
                    ],
                    set_={"multiplier": weight.multiplier},
                )
            )

    async def remove_role_weight(self, guild_id: int, kind: VoteKind, role_id: int) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                delete(GuildVoteRoleWeight).where(
                    GuildVoteRoleWeight.guild_id == guild_id,
                    GuildVoteRoleWeight.kind == kind,
                    GuildVoteRoleWeight.role_id == role_id,
                )
            )

    async def reset_configuration(self, guild_id: int, kind: VoteKind | None = None) -> None:
        async with self._session_factory.begin() as session:
            emoji = delete(GuildVoteEmoji).where(GuildVoteEmoji.guild_id == guild_id)
            weights = delete(GuildVoteRoleWeight).where(GuildVoteRoleWeight.guild_id == guild_id)
            if kind is not None:
                emoji = emoji.where(GuildVoteEmoji.kind == kind)
                weights = weights.where(GuildVoteRoleWeight.kind == kind)
            await session.execute(emoji)
            await session.execute(weights)

    @staticmethod
    async def _get_session_row(
        session: AsyncSession, message_id: int, *, for_update: bool = False
    ) -> VoteSession | None:
        statement = (
            select(VoteSession)
            .join(DiscordPost, DiscordPost.resource_key == cast_sql(VoteSession.id, Text))
            .where(
                DiscordPost.message_id == message_id,
                DiscordPost.resource_kind == "vote_session",
                DiscordPost.suppressed_at.is_(None),
            )
        )
        if for_update:
            statement = statement.with_for_update(of=VoteSession)
        return await _VoteSessionModelRepository(session=session).get_one_or_none(statement=statement)

    @staticmethod
    async def _to_snapshot(session: AsyncSession, row: VoteSession) -> VoteSessionSnapshot:
        # The guild comes from the message fact; the post says which messages are ours.
        locations = (
            await session.execute(
                select(DiscordPost.message_id, DiscordPost.channel_id, Message.guild_id)
                .join(Message, Message.id == DiscordPost.message_id)
                .where(
                    DiscordPost.resource_kind == "vote_session",
                    DiscordPost.resource_key == str(row.id),
                    DiscordPost.suppressed_at.is_(None),
                )
                .order_by(DiscordPost.message_id)
            )
        ).all()
        vote_messages = tuple(
            VoteMessage(message_id, channel_id, guild_id or 0) for message_id, channel_id, guild_id in locations
        )
        vote_rows = (
            await session.scalars(select(Vote).where(Vote.vote_session_id == row.id).order_by(Vote.account_id))
        ).all()
        selections = tuple(
            VoteSelection(
                vote.account_id,
                vote.guild_id,
                vote.option_id,
                vote.emoji,
                vote.weight,
            )
            for vote in vote_rows
        )
        option_by_key = {(option.identifier, option.guild_id): option for option in row.options}
        signed_votes: dict[int, float] = {}
        for vote in vote_rows:
            option = option_by_key.get((vote.option_id, vote.guild_id)) or option_by_key.get((vote.option_id, 0))
            direction = -1 if option is not None and option.choice is VoteChoice.DENY else 1
            signed_votes[vote.account_id] = vote.weight * direction

        target, poll = await VoteRepository._load_target(session, row)

        return VoteSessionSnapshot(
            id=row.id,
            author_account_id=row.author_account_id,
            kind=row.kind,
            status=row.status,
            result=row.result,
            pass_threshold=row.pass_threshold,
            fail_threshold=row.fail_threshold,
            votes=signed_votes,
            messages=vote_messages,
            options=tuple(
                VoteOption(
                    option.emoji,
                    option.choice,
                    option.multiplier,
                    option.identifier,
                    option.guild_id or None,
                    option.label,
                    option.position,
                )
                for option in row.options
            ),
            target=target,
            selections=selections,
            poll=poll,
        )

    @staticmethod
    async def _load_target(session: AsyncSession, row: VoteSession) -> tuple[VoteTarget, GenericPoll | None]:
        """Load the typed target, or the poll metadata for the kind that has no target."""
        match row.kind:
            case VoteKind.BUILD:
                build_id = await session.scalar(
                    select(BuildVoteSession.build_id).where(BuildVoteSession.vote_session_id == row.id)
                )
                return (None if build_id is None else BuildVoteTarget(build_id)), None
            case VoteKind.DELETE_LOG:
                target_row = (
                    (
                        await session.execute(
                            select(
                                DeleteLogVoteSession.target_message_id,
                                DeleteLogVoteSession.target_channel_id,
                                DeleteLogVoteSession.target_server_id,
                            ).where(DeleteLogVoteSession.vote_session_id == row.id)
                        )
                    )
                    .tuples()
                    .one_or_none()
                )
                return (None if target_row is None else DeleteLogVoteTarget(*target_row)), None
            case VoteKind.GENERIC:
                generic = await session.scalar(
                    select(GenericVoteSession).where(GenericVoteSession.vote_session_id == row.id)
                )
                if generic is None:
                    return None, None
                return None, GenericPoll(
                    generic.question, generic.visibility, generic.deadline, generic.guild_id, generic.scope
                )
