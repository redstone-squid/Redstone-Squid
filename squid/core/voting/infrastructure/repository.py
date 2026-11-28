"""SQLAlchemy voting repository."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from itertools import batched
from typing import Any

from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from sqlalchemy import BigInteger, Select, Text, delete, func, insert, select
from sqlalchemy import cast as cast_sql
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from whenever import Instant

from squid.core.accounts.domain import IdentityProvider
from squid.core.accounts.models import Account, AccountIdentity, AccountProfile
from squid.core.builds.models import Build
from squid.core.errors import DataIntegrityError
from squid.core.messages.errors import MessageNotFoundError
from squid.core.messages.models import Message
from squid.core.pagination import Page, PageSelector
from squid.core.persistence.paging import KeysetOrder
from squid.core.posts.models import DiscordPost
from squid.core.scores import round_score
from squid.core.settings.infrastructure.repository import ensure_server_settings
from squid.core.settings.models import ServerSetting
from squid.core.submissions.models.reviews import BuildSubmission
from squid.core.voting.domain import (
    DEFAULT_VOTE_MENU,
    BallotChange,
    BuildEditVoteTarget,
    BuildVoteTarget,
    DeleteLogVoteTarget,
    EmojiPreset,
    GenericPoll,
    GuildMember,
    PollScope,
    ReviewFilters,
    ReviewSort,
    RoleWeight,
    StoredVoteMutation,
    VoteAlias,
    VoteChange,
    VoteChoice,
    VoteKind,
    VoteMenu,
    VoteMessage,
    VoteOption,
    Voter,
    VoteSelection,
    VoteSessionResult,
    VoteSessionSnapshot,
    VoteStatus,
    VoteTarget,
    VoteVisibility,
)
from squid.core.voting.errors import VoteSessionNotFoundError
from squid.core.voting.models import (
    BuildVoteSession,
    DeleteLogVoteSession,
    GenericVoteSession,
    GuildVoteEmoji,
    GuildVoteRoleWeight,
    Vote,
    VoteBallotEvent,
    VoterGuildMember,
    VoteSession,
    VoteSessionOption,
    VoteSessionOptionAlias,
)

_LOAD_MENU = (selectinload(VoteSession.options), selectinload(VoteSession.aliases))


class _VoteSessionModelRepository(SQLAlchemyAsyncRepository[VoteSession]):
    model_type = VoteSession


class VoteRepository:
    """Store voting sessions, their ballots, and the messages that display them in PostgreSQL.

    VoteService supplies eligibility decisions and ballot weights. This
    repository records ballots and closes build or deletion votes when their
    stored net score reaches a threshold. Generic polls close through an explicit
    request or the service's deadline scheduler instead.

    Before changing ballots or closing a vote, lock its
    VoteSession row. Concurrent updates to the same voting session then take
    turns, so the threshold check sees earlier committed changes. The database
    session comes from the surrounding unit of work, which also commits or rolls
    back the transaction; repository methods do not commit on their own.
    """

    def __init__(self, session: Callable[[], AsyncSession]):
        self._session = session

    async def create_build_edit_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        target: BuildEditVoteTarget,
        submitter_account_id: int,
    ) -> int:
        session = self._session()
        guild_ids = (
            await session.scalars(
                select(GuildVoteEmoji.guild_id).where(GuildVoteEmoji.kind == VoteKind.BUILD_EDIT).distinct()
            )
        ).all()
        presets = await self.get_emoji_presets(guild_ids, VoteKind.BUILD_EDIT) if guild_ids else {}
        menu = VoteMenu.shared((DEFAULT_VOTE_MENU, *(preset.menu for preset in presets.values()))).validated(
            VoteKind.BUILD_EDIT
        )
        session_id = await self._create_session(
            session, author_account_id, VoteKind.BUILD_EDIT, pass_threshold, fail_threshold, menu
        )
        assert target.submission_id is not None
        submission = BuildSubmission(
            id=target.submission_id,
            build_id=target.build_id,
            submitter_account_id=submitter_account_id,
            edit_vote_id=session_id,
            base_revision=target.revision,
            content={"patch": dict(target.patch), "changes": list(target.changes)},
        )
        session.add(submission)
        await session.execute(
            insert(BuildVoteSession).values(
                vote_session_id=session_id,
                build_id=target.build_id,
                changes=list(target.changes),
                public_suggestion=target.public_suggestion,
                proposal_revision=target.revision,
                submission_id=submission.id,
                proposal_patch=dict(target.patch),
            )
        )
        return session_id

    async def get_or_create_build_submission_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        menu: VoteMenu = DEFAULT_VOTE_MENU,
    ) -> int:
        """Return the earliest build review session, creating it under a per-build transaction lock.

        An existing session is reused even when closed. The caller must keep the
        transaction open through commit to serialize concurrent creation attempts.
        """
        menu = menu.validated(VoteKind.BUILD)
        session = self._session()
        lock_key = f"build-submission-vote:{build_id}"
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))
        existing = await session.scalar(
            select(BuildVoteSession.vote_session_id)
            .join(VoteSession, VoteSession.id == BuildVoteSession.vote_session_id)
            .where(
                BuildVoteSession.build_id == build_id,
                VoteSession.kind == VoteKind.BUILD,
                BuildVoteSession.reviewed_revision
                == select(Build.revision).where(Build.id == build_id).scalar_subquery(),
            )
            .order_by(BuildVoteSession.vote_session_id)
            .limit(1)
        )
        if existing is not None:
            return existing
        session_id = await self._create_session(
            session, author_account_id, VoteKind.BUILD, pass_threshold, fail_threshold, menu
        )
        await session.execute(
            insert(BuildVoteSession).values(
                vote_session_id=session_id,
                build_id=build_id,
                changes=list(changes),
                submission_id=select(BuildSubmission.id)
                .where(BuildSubmission.build_id == build_id, BuildSubmission.edit_vote_id.is_(None))
                .limit(1)
                .scalar_subquery(),
                reviewed_revision=select(Build.revision).where(Build.id == build_id).scalar_subquery(),
            )
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
        menu: VoteMenu = DEFAULT_VOTE_MENU,
    ) -> int:
        menu = menu.validated(VoteKind.BUILD)
        session = self._session()
        session_id = await self._create_session(
            session, author_account_id, VoteKind.BUILD, pass_threshold, fail_threshold, menu
        )
        await session.execute(
            insert(BuildVoteSession).values(
                vote_session_id=session_id,
                build_id=build_id,
                changes=list(changes),
                submission_id=select(BuildSubmission.id)
                .where(BuildSubmission.build_id == build_id, BuildSubmission.edit_vote_id.is_(None))
                .limit(1)
                .scalar_subquery(),
                reviewed_revision=select(Build.revision).where(Build.id == build_id).scalar_subquery(),
            )
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
        menu: VoteMenu = DEFAULT_VOTE_MENU,
    ) -> int:
        menu = menu.validated(VoteKind.DELETE_LOG)
        session = self._session()
        # Match build-review creation: keep this lock through the caller's commit.
        lock_key = f"delete-log-vote:{message_id}"
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))
        existing = await session.scalar(
            select(DeleteLogVoteSession.vote_session_id)
            .where(DeleteLogVoteSession.target_message_id == message_id)
            .order_by(DeleteLogVoteSession.vote_session_id)
            .limit(1)
        )
        if existing is not None:
            return existing
        session_id = await self._create_session(
            session, author_account_id, VoteKind.DELETE_LOG, pass_threshold, fail_threshold, menu
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
        menu: VoteMenu,
        guild_id: int | None = None,
        scope: PollScope = PollScope.GUILD,
    ) -> int:
        menu = menu.validated(VoteKind.GENERIC)
        session = self._session()
        session_id = await self._create_session(session, author_account_id, VoteKind.GENERIC, None, None, menu)
        await ensure_server_settings(session, (guild_id,))
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

    async def attach_message(self, vote_session_id: int, message_id: int) -> None:
        """Attach one recorded message to a vote session, tolerating an exact replay.

        A message belongs to at most one resource, while the partial unique index on
        ``discord_posts`` prevents a second live card for this session in the same
        channel. Retrying the same pair is a no-op; silently accepting a different
        resource for the same message would make the caller believe the wrong card was
        attached, so that case is rejected explicitly.
        """
        session = self._session()
        if await session.scalar(select(VoteSession.id).where(VoteSession.id == vote_session_id)) is None:
            raise VoteSessionNotFoundError(vote_session_id)
        channel_id = await session.scalar(select(Message.channel_id).where(Message.id == message_id))
        if channel_id is None:
            raise MessageNotFoundError(message_id)

        expected = (channel_id, "vote_session", str(vote_session_id), "vote_card")
        existing = await session.execute(
            select(
                DiscordPost.channel_id,
                DiscordPost.resource_kind,
                DiscordPost.resource_key,
                DiscordPost.surface,
            ).where(DiscordPost.message_id == message_id)
        )
        existing_values = existing.one_or_none()
        if existing_values is not None:
            if existing_values == expected:
                return
            msg = "Discord message is already attached to a different resource."
            raise DataIntegrityError(
                msg,
                context={"message_id": message_id, "vote_session_id": vote_session_id},
            )

        attached = await session.scalar(
            pg_insert(DiscordPost)
            .values(
                message_id=message_id,
                channel_id=channel_id,
                resource_kind="vote_session",
                resource_key=str(vote_session_id),
                surface="vote_card",
                applied_revision=0,
            )
            .on_conflict_do_nothing(index_elements=[DiscordPost.message_id])
            .returning(DiscordPost.message_id)
        )
        if attached is not None:
            return

        # A concurrent exact replay may have won after the read above. The
        # primary-key conflict is the only one the insert suppresses; the live
        # resource/channel uniqueness constraint still rejects a second card.
        raced = await session.execute(
            select(
                DiscordPost.channel_id,
                DiscordPost.resource_kind,
                DiscordPost.resource_key,
                DiscordPost.surface,
            ).where(DiscordPost.message_id == message_id)
        )
        if raced.one_or_none() != expected:
            msg = "Discord message was concurrently attached to a different resource."
            raise DataIntegrityError(
                msg,
                context={"message_id": message_id, "vote_session_id": vote_session_id},
            )

    @staticmethod
    async def _create_session(
        session: AsyncSession,
        author_account_id: int,
        kind: VoteKind,
        pass_threshold: int | None,
        fail_threshold: int | None,
        menu: VoteMenu,
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
                    "identifier": option.id,
                    "choice": option.choice.value,
                    "label": option.label,
                    "multiplier": option.multiplier,
                    "position": position,
                }
                for position, option in enumerate(menu.options)
            ],
        )
        positions: dict[int | None, int] = {}
        aliases = []
        for alias in menu.aliases:
            position = positions[alias.guild_id] = positions.get(alias.guild_id, -1) + 1
            aliases.append(
                {
                    "vote_session_id": session_id,
                    "option_identifier": alias.option_id,
                    "guild_id": alias.guild_id,
                    "emoji": alias.emoji,
                    "position": position,
                }
            )
        if aliases:
            await session.execute(insert(VoteSessionOptionAlias), aliases)
        return session_id

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None:
        session = self._session()
        statement = (
            select(VoteSession)
            .options(*_LOAD_MENU)
            .join(DiscordPost, DiscordPost.resource_key == cast_sql(VoteSession.id, Text))
            .where(
                DiscordPost.message_id == message_id,
                DiscordPost.resource_kind == "vote_session",
                DiscordPost.suppressed_at.is_(None),
            )
        )
        row = await session.scalar(statement)
        return None if row is None else await self._to_snapshot(session, row)

    async def get_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        session = self._session()
        row = await _VoteSessionModelRepository(session=session, load=list(_LOAD_MENU)).get_one_or_none(
            id=vote_session_id
        )
        return None if row is None else await self._to_snapshot(session, row)

    async def list_open(self, kind: VoteKind) -> Sequence[VoteSessionSnapshot]:
        session = self._session()
        rows = await _VoteSessionModelRepository(session=session, load=list(_LOAD_MENU)).get_many(
            VoteSession.status == VoteStatus.OPEN, VoteSession.kind == kind
        )
        return [await self._to_snapshot(session, row) for row in rows]

    async def list_reviews(
        self, filters: ReviewFilters, *, sort: ReviewSort, selector: PageSelector, page_size: int
    ) -> Page[VoteSessionSnapshot]:
        session = self._session()
        order = KeysetOrder(sort, VoteSession.id, VoteSession.id, descending=sort is ReviewSort.NEWEST)
        total = await session.scalar(_reviews(select(func.count()).select_from(VoteSession), filters))
        page = await order.page(
            session,
            _reviews(select(VoteSession).options(*_LOAD_MENU), filters),
            selector,
            page_size=page_size,
            total=total or 0,
        )
        return replace(page, items=tuple([await self._to_snapshot(session, row) for row in page.items]))

    async def transition_poll(
        self,
        snapshot: VoteSessionSnapshot,
        status: VoteStatus,
        *,
        retry_at: Instant | None = None,
        unreadable: Sequence[int] = (),
        deadline: Instant | None = None,
        actor_id: int | None = None,
        reason: str = "",
        claim: bool = False,
    ) -> VoteSessionSnapshot | None:
        session = self._session()
        row = await self._lock_session_row(session, snapshot.id)
        poll = await session.get(GenericVoteSession, snapshot.id)
        if (
            row is None
            or poll is None
            or snapshot.poll is None
            or row.status != snapshot.status
            or poll.count_revision != snapshot.poll.count_revision
        ):
            return None
        now = Instant.now().round("microsecond", mode="floor")
        row.status = status
        poll.count_revision += 1
        poll.retry_at = retry_at
        if claim:
            poll.voting_ended_at = None
            poll.count_attempts = 1 if snapshot.status is VoteStatus.REVIEW else poll.count_attempts + 1
        else:
            poll.unreadable_cards = list(unreadable)
        if status in (VoteStatus.REVIEW, VoteStatus.CLOSED, VoteStatus.VOID) and poll.voting_ended_at is None:
            poll.voting_ended_at = now
        if deadline is not None:
            poll.deadline = deadline
            poll.count_attempts = 0
            poll.voting_ended_at = None
        if status in (VoteStatus.CLOSED, VoteStatus.VOID):
            row.result = VoteSessionResult.CANCELLED
        if actor_id is not None:
            poll.review_history = [
                *poll.review_history,
                dict(
                    actor=actor_id,
                    at=str(now),
                    status=status.value,
                    reason=reason,
                    prior_deadline=str(snapshot.poll.deadline),
                    unreadable=list(snapshot.poll.unreadable_cards),
                ),
            ]
        await session.flush()
        return await self._to_snapshot(session, row)

    async def pending_reviews(self, guild_id: int) -> Sequence[int]:
        attached_in_guild = (
            select(DiscordPost.resource_key)
            .join(Message, Message.id == DiscordPost.message_id)
            .where(DiscordPost.resource_kind == "vote_session", Message.guild_id == guild_id)
        )
        rows = await self._session().scalars(
            select(VoteSession.id)
            .join(GenericVoteSession, GenericVoteSession.vote_session_id == VoteSession.id)
            .where(
                VoteSession.status == VoteStatus.REVIEW,
                (GenericVoteSession.guild_id == guild_id)
                | (GenericVoteSession.guild_id.is_(None) & cast_sql(VoteSession.id, Text).in_(attached_in_guild)),
            )
            .order_by(VoteSession.id)
        )
        return rows.all()

    async def list_due(self, now: Instant) -> Sequence[VoteSessionSnapshot]:
        session = self._session()
        rows = (
            await session.scalars(
                select(VoteSession)
                .options(*_LOAD_MENU)
                .join(GenericVoteSession, GenericVoteSession.vote_session_id == VoteSession.id)
                .where(
                    ((VoteSession.status == VoteStatus.OPEN) & (GenericVoteSession.deadline <= now))
                    | ((VoteSession.status == VoteStatus.COUNTING) & (GenericVoteSession.retry_at <= now))
                )
            )
        ).all()
        return [await self._to_snapshot(session, row) for row in rows]

    async def cast_vote(
        self,
        vote_session_id: int,
        voter: Voter,
        option: VoteOption,
        weight: float,
        *,
        guild_id: int | None = None,
        emoji: str | None = None,
        toggle: bool = True,
        count_revision: int | None = None,
    ) -> StoredVoteMutation | None:
        """Toggle or replace a ballot in an open session, returning None if missing or closed.

        `option` must be one of the session's options. `guild_id` and `emoji` record the
        card and alias the ballot was cast with, if any. Selecting the stored option
        again removes the ballot unless `toggle` is false. Returned weights are
        positive magnitudes; threshold scoring supplies the option's sign.
        """
        session = self._session()
        row = await self._lock_session_row(session, vote_session_id)
        if row is None:
            return None
        if count_revision is not None:
            poll = await session.get(GenericVoteSession, vote_session_id)
            if row.status is not VoteStatus.COUNTING or poll is None or poll.count_revision != count_revision:
                return None
        elif row.status is not VoteStatus.OPEN:
            return None
        if all(stored.identifier != option.id for stored in row.options):
            # Options are written once with the session, so the service cannot have resolved
            # an option the session does not store.
            msg = "Vote option is not one of the session's options."
            raise DataIntegrityError(msg, context={"vote_session_id": vote_session_id, "option_id": option.id})
        if guild_id is not None:
            await ensure_server_settings(session, (guild_id,))
        previous = await self._ballot(session, row.id, voter)
        previous_weight = previous.weight if previous is not None else None
        # Aliases of the same option toggle the same ballot: ✅ after 👍 still means "approve".
        if toggle and previous is not None and previous.option_identifier == option.id:
            await session.delete(previous)
            current_weight = None
            event = {"change": BallotChange.CLEAR, "guild_id": guild_id, "emoji": emoji}
        else:
            values = {"option_identifier": option.id, "guild_id": guild_id, "emoji": emoji, "weight": weight}
            if previous is None:
                session.add(
                    Vote(
                        vote_session_id=row.id,
                        account_id=voter.account_id,
                        discord_user_id=voter.discord_user_id,
                        option_identifier=option.id,
                        guild_id=guild_id,
                        emoji=emoji,
                        weight=weight,
                    )
                )
            else:
                for name, value in values.items():
                    setattr(previous, name, value)
            current_weight = weight
            event = {"change": BallotChange.CAST, **values}
        await session.flush()
        await session.execute(insert(VoteBallotEvent).values(vote_session_id=row.id, **_voter_columns(voter), **event))
        just_closed = await self._close_at_threshold(session, row)
        return StoredVoteMutation(await self._to_snapshot(session, row), previous_weight, current_weight, just_closed)

    async def clear_vote(
        self, vote_session_id: int, voter: Voter, *, change: BallotChange = BallotChange.CLEAR
    ) -> StoredVoteMutation | None:
        """Remove a ballot from an open session and check thresholds.

        `change` records why: the voter withdrew it, or staff voided it. Removing a
        ballot the voter does not have records nothing.
        """
        session = self._session()
        row = await self._lock_session_row(session, vote_session_id)
        if row is None or row.status is not VoteStatus.OPEN:
            return None
        previous = await self._ballot(session, row.id, voter)
        previous_weight = None if previous is None else previous.weight
        if previous is not None:
            await session.delete(previous)
            await session.flush()
            await session.execute(
                insert(VoteBallotEvent).values(vote_session_id=row.id, **_voter_columns(voter), change=change)
            )
        just_closed = await self._close_at_threshold(session, row)
        return StoredVoteMutation(await self._to_snapshot(session, row), previous_weight, None, just_closed)

    @staticmethod
    async def _ballot(session: AsyncSession, vote_session_id: int, voter: Voter) -> Vote | None:
        """Return the voter's ballot in a locked session.

        An account first takes over any ballot one of its Discord identities cast
        before the account could vote, so one person never holds two ballots. The
        account's own ballot wins if it already has one.
        """
        if voter.account_id is None:
            return await session.scalar(
                select(Vote).where(
                    Vote.vote_session_id == vote_session_id, Vote.discord_user_id == voter.discord_user_id
                )
            )
        own = await session.scalar(
            select(Vote).where(Vote.vote_session_id == vote_session_id, Vote.account_id == voter.account_id)
        )
        identities = select(cast_sql(AccountIdentity.subject, BigInteger)).where(
            AccountIdentity.account_id == voter.account_id, AccountIdentity.provider == IdentityProvider.DISCORD
        )
        placeholders = (
            await session.scalars(
                select(Vote).where(Vote.vote_session_id == vote_session_id, Vote.discord_user_id.in_(identities))
            )
        ).all()
        for placeholder in placeholders:
            if own is None:
                placeholder.account_id, placeholder.discord_user_id = voter.account_id, None
                own = placeholder
            else:
                await session.delete(placeholder)
        if placeholders:
            await session.flush()
        return own

    async def close(self, vote_session_id: int) -> StoredVoteMutation | None:
        """Close an open session with the CANCELLED result; return None if missing or already closed."""
        session = self._session()
        row = await self._lock_session_row(session, vote_session_id)
        if row is None or row.status is not VoteStatus.OPEN:
            return None
        row.status = VoteStatus.CLOSED
        row.result = VoteSessionResult.CANCELLED
        return StoredVoteMutation(
            session=await self._to_snapshot(session, row),
            previous_weight=None,
            current_weight=None,
            just_closed=True,
        )

    @staticmethod
    async def _close_at_threshold(session: AsyncSession, row: VoteSession) -> bool:
        """Finalize a locked open session once its current tally reaches a threshold."""
        if row.status is not VoteStatus.OPEN or row.pass_threshold is None or row.fail_threshold is None:
            return False
        selections = await session.execute(
            select(Vote.weight, VoteSessionOption.choice)
            .join(
                VoteSessionOption,
                (VoteSessionOption.vote_session_id == Vote.vote_session_id)
                & (VoteSessionOption.identifier == Vote.option_identifier),
            )
            .where(Vote.vote_session_id == row.id)
        )
        net = round_score(sum(weight if choice is VoteChoice.APPROVE else -weight for weight, choice in selections))
        if net >= row.pass_threshold:
            result = VoteSessionResult.APPROVED
        elif net <= row.fail_threshold:
            result = VoteSessionResult.DENIED
        else:
            return False
        row.status = VoteStatus.CLOSED
        row.result = result
        return True

    async def get_member(self, guild_id: int, account_id: int) -> GuildMember | None:
        row = await self._session().get(VoterGuildMember, (guild_id, account_id))
        return None if row is None else GuildMember(row.discord_user_id, frozenset(row.role_ids), row.manage_server)

    async def record_members(self, guild_id: int, members: Sequence[GuildMember], *, complete: bool) -> None:
        """Store guild roles for members with a linked Discord account."""
        session = self._session()
        synced_at = Instant.now().round("microsecond", mode="floor")
        by_discord_id = {member.discord_user_id: member for member in members}
        for batch in batched(by_discord_id, 5000, strict=False):
            accounts = await session.execute(
                select(AccountIdentity.subject, Account.id)
                .join(Account, Account.id == AccountIdentity.account_id)
                .where(
                    AccountIdentity.provider == IdentityProvider.DISCORD,
                    AccountIdentity.subject.in_([str(discord_id) for discord_id in batch]),
                )
            )
            rows = {
                account_id: {
                    "guild_id": guild_id,
                    "account_id": account_id,
                    "discord_user_id": int(subject),
                    "role_ids": sorted(by_discord_id[int(subject)].role_ids),
                    "manage_server": by_discord_id[int(subject)].manage_server,
                    "synced_at": synced_at,
                }
                for subject, account_id in accounts
            }
            if rows:
                statement = pg_insert(VoterGuildMember).values(list(rows.values()))
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[VoterGuildMember.guild_id, VoterGuildMember.account_id],
                        set_={
                            column: statement.excluded[column]
                            for column in ("discord_user_id", "role_ids", "manage_server", "synced_at")
                        },
                    )
                )
        if complete:
            await session.execute(
                delete(VoterGuildMember).where(
                    VoterGuildMember.guild_id == guild_id, VoterGuildMember.synced_at < synced_at
                )
            )

    async def forget_member(self, guild_id: int, discord_user_id: int) -> None:
        await self._session().execute(
            delete(VoterGuildMember).where(
                VoterGuildMember.guild_id == guild_id, VoterGuildMember.discord_user_id == discord_user_id
            )
        )

    async def lock_configuration(self, guild_id: int) -> None:
        session = self._session()
        await ensure_server_settings(session, (guild_id,))
        await session.scalar(select(ServerSetting).where(ServerSetting.server_id == guild_id).with_for_update())

    async def _touch_configuration(self, guild_id: int) -> None:
        # Direct child SQL does not invoke the parent's ORM version mapper.
        row = await self._session().scalar(select(ServerSetting).where(ServerSetting.server_id == guild_id))
        assert row is not None
        row.revision += 1

    async def get_emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset | None:
        return (await self.get_emoji_presets((guild_id,), kind)).get(guild_id)

    async def get_emoji_presets(self, guild_ids: Sequence[int], kind: VoteKind) -> dict[int, EmojiPreset]:
        """Read each guild's stored menu; rows sharing an identifier are aliases of one option."""
        rows = (
            await self._session().scalars(
                select(GuildVoteEmoji)
                .where(GuildVoteEmoji.guild_id.in_(guild_ids), GuildVoteEmoji.kind == kind)
                .order_by(GuildVoteEmoji.guild_id, GuildVoteEmoji.position)
            )
        ).all()
        rows_by_guild: dict[int, list[tuple[VoteOption, VoteAlias]]] = {}
        for row in rows:
            rows_by_guild.setdefault(row.guild_id, []).append(
                (
                    VoteOption(row.identifier, row.choice, label=row.label),
                    VoteAlias(row.identifier, row.emoji, row.guild_id),
                )
            )
        return {
            guild_id: EmojiPreset(guild_id, kind, VoteMenu.of(guild_rows))
            for guild_id, guild_rows in rows_by_guild.items()
        }

    async def set_emoji_preset(self, preset: EmojiPreset) -> None:
        await self.lock_configuration(preset.guild_id)
        await self._touch_configuration(preset.guild_id)
        session = self._session()
        await ensure_server_settings(session, (preset.guild_id,))
        await session.execute(
            delete(GuildVoteEmoji).where(GuildVoteEmoji.guild_id == preset.guild_id, GuildVoteEmoji.kind == preset.kind)
        )
        await session.execute(
            insert(GuildVoteEmoji),
            [
                {
                    "guild_id": preset.guild_id,
                    "kind": preset.kind.value,
                    "identifier": option.id,
                    "emoji": alias.emoji,
                    "choice": option.choice.value,
                    "label": option.label,
                    "position": position,
                }
                for position, (option, alias) in enumerate(preset.menu.rows())
            ],
        )

    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> Sequence[RoleWeight]:
        session = self._session()
        rows = (
            await session.scalars(
                select(GuildVoteRoleWeight).where(
                    GuildVoteRoleWeight.guild_id == guild_id, GuildVoteRoleWeight.kind == kind
                )
            )
        ).all()
        return [RoleWeight(row.guild_id, row.kind, row.role_id, row.multiplier) for row in rows]

    async def set_role_weight(self, weight: RoleWeight) -> None:
        await self.lock_configuration(weight.guild_id)
        await self._touch_configuration(weight.guild_id)
        session = self._session()
        await ensure_server_settings(session, (weight.guild_id,))
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
        await self.lock_configuration(guild_id)
        await self._touch_configuration(guild_id)
        session = self._session()
        await session.execute(
            delete(GuildVoteRoleWeight).where(
                GuildVoteRoleWeight.guild_id == guild_id,
                GuildVoteRoleWeight.kind == kind,
                GuildVoteRoleWeight.role_id == role_id,
            )
        )

    async def reset_configuration(self, guild_id: int, kind: VoteKind | None = None) -> None:
        await self.lock_configuration(guild_id)
        await self._touch_configuration(guild_id)
        session = self._session()
        emoji = delete(GuildVoteEmoji).where(GuildVoteEmoji.guild_id == guild_id)
        weights = delete(GuildVoteRoleWeight).where(GuildVoteRoleWeight.guild_id == guild_id)
        if kind is not None:
            emoji = emoji.where(GuildVoteEmoji.kind == kind)
            weights = weights.where(GuildVoteRoleWeight.kind == kind)
        await session.execute(emoji)
        await session.execute(weights)

    @staticmethod
    async def _lock_session_row(session: AsyncSession, vote_session_id: int) -> VoteSession | None:
        """Load a session with its options and lock it until the transaction ends."""
        return await session.scalar(
            select(VoteSession)
            .options(*_LOAD_MENU)
            .where(VoteSession.id == vote_session_id)
            .with_for_update(of=VoteSession)
        )

    @staticmethod
    async def _to_snapshot(session: AsyncSession, row: VoteSession) -> VoteSessionSnapshot:
        """Load messages, ballots, and kind-specific data for the voting session.

        Derive signed score weights from the referenced alias choice;
        stored ballot weights remain positive.
        """
        # DiscordPost associates a message with the vote; Message supplies the guild where it was sent.
        locations = (
            await session.execute(
                select(DiscordPost.message_id, DiscordPost.channel_id, Message.guild_id)
                .join(Message, Message.id == DiscordPost.message_id)
                .where(
                    DiscordPost.resource_kind == "vote_session",
                    DiscordPost.resource_key == str(row.id),
                    # A deleted card is evidence of an incomplete final count.
                    (DiscordPost.suppressed_at.is_(None) | (row.status in (VoteStatus.COUNTING, VoteStatus.REVIEW))),
                )
                .order_by(DiscordPost.message_id)
            )
        ).all()
        vote_messages = tuple(
            VoteMessage(message_id, channel_id, guild_id or 0) for message_id, channel_id, guild_id in locations
        )
        vote_rows = (
            await session.execute(
                select(Vote, VoteSessionOption.choice, AccountProfile.display_name)
                .join(
                    VoteSessionOption,
                    (VoteSessionOption.vote_session_id == Vote.vote_session_id)
                    & (VoteSessionOption.identifier == Vote.option_identifier),
                )
                .outerjoin(
                    AccountProfile, (AccountProfile.account_id == Vote.account_id) & AccountProfile.hidden.is_(False)
                )
                .where(Vote.vote_session_id == row.id)
                .order_by(Vote.id)
            )
        ).all()
        selections = tuple(
            VoteSelection(_voter(vote), vote.guild_id, vote.option_identifier, vote.emoji, vote.weight, name)
            for vote, _, name in vote_rows
        )
        signed_votes = {
            _voter(vote): vote.weight * (-1 if choice is VoteChoice.DENY else 1) for vote, choice, _ in vote_rows
        }

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
            menu=VoteMenu(
                tuple(
                    VoteOption(option.identifier, option.choice, option.label, option.multiplier, option.position)
                    for option in row.options
                ),
                tuple(
                    VoteAlias(alias.option_identifier, alias.emoji, alias.guild_id, alias.position)
                    for alias in row.aliases
                ),
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
                target = await session.scalar(
                    select(BuildVoteSession).where(BuildVoteSession.vote_session_id == row.id)
                )
                return (
                    None
                    if target is None
                    else BuildVoteTarget(target.build_id, target.reviewed_revision, target.submission_id)
                ), None
            case VoteKind.BUILD_EDIT:
                proposal = await session.scalar(
                    select(BuildVoteSession).where(BuildVoteSession.vote_session_id == row.id)
                )
                if proposal is None or proposal.proposal_revision is None or proposal.proposal_patch is None:
                    return None, None
                return BuildEditVoteTarget(
                    proposal.build_id,
                    proposal.proposal_revision,
                    proposal.proposal_patch,
                    tuple((field, before, after) for field, before, after in proposal.changes),
                    public_suggestion=proposal.public_suggestion,
                    submission_id=proposal.submission_id,
                ), None
            case VoteKind.DELETE_LOG:
                target_row = (
                    await session.execute(
                        select(
                            DeleteLogVoteSession.target_message_id,
                            DeleteLogVoteSession.target_channel_id,
                            DeleteLogVoteSession.target_server_id,
                        ).where(DeleteLogVoteSession.vote_session_id == row.id)
                    )
                ).one_or_none()
                return (None if target_row is None else DeleteLogVoteTarget(*target_row)), None
            case VoteKind.GENERIC:
                generic = await session.scalar(
                    select(GenericVoteSession).where(GenericVoteSession.vote_session_id == row.id)
                )
                if generic is None:
                    return None, None
                return None, GenericPoll(
                    generic.question,
                    generic.visibility,
                    generic.deadline,
                    generic.guild_id,
                    generic.scope,
                    generic.voting_ended_at,
                    generic.count_revision,
                    generic.count_attempts,
                    generic.retry_at,
                    tuple(generic.unreadable_cards),
                )


def _reviews[S: Select[Any]](statement: S, filters: ReviewFilters) -> S:
    statement = statement.where(VoteSession.kind.in_(filters.vote_kinds))
    if filters.status is not None:
        statement = statement.where(VoteSession.status == filters.status)
    return statement


def _voter(vote: Vote) -> Voter:
    return Voter(account_id=vote.account_id, discord_user_id=vote.discord_user_id)


def _voter_columns(voter: Voter) -> dict[str, int | None]:
    return {"account_id": voter.account_id, "discord_user_id": voter.discord_user_id}
