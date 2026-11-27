"""Voting snapshots and database fixtures shared across domain and adapter tests."""

from collections.abc import Mapping, Sequence
from copy import copy
from typing import Self, cast

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.actors import UserActor
from squid.core.messages.models import Message
from squid.core.permissions.application.ports import PermissionSubject, SubjectRecords
from squid.core.permissions.models import PermissionGrant
from squid.core.posts.models import DiscordPost
from squid.core.voting.domain import (
    DEFAULT_VOTE_MENU,
    BuildVoteTarget,
    DeleteLogVoteTarget,
    GenericPoll,
    PollScope,
    VoteAlias,
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
from squid.core.voting.models import DeleteLogVoteSession, Vote, VoteSession, VoteSessionOption, VoteSessionOptionAlias
from tests.support.unit_of_work import PublishingFakeUnitOfWork

GUILD_ID = 503
CHANNEL_ID = 502
TARGET_MESSAGE_ID = 501
BUILD_ID = 42
AUTHOR_ACCOUNT_IDS = (99, 100)
VOTER_ACCOUNT_IDS = (7, 8)

DEFAULT_BUILD_TARGET = BuildVoteTarget(42)
DEFAULT_DELETE_LOG_TARGET = DeleteLogVoteTarget(TARGET_MESSAGE_ID, CHANNEL_ID, GUILD_ID)
_INFER_TARGET = object()


def generic_menu(guild_id: int | None = 10, ids: tuple[str, str] = ("one", "two")) -> VoteMenu:
    """A two-option poll menu aliased by 1️⃣ and 2️⃣ in `guild_id`."""
    return VoteMenu.of(
        (VoteOption(option_id, VoteChoice.GENERIC, label=option_id.title()), VoteAlias(option_id, emoji, guild_id))
        for option_id, emoji in zip(ids, ("1️⃣", "2️⃣"), strict=True)
    )


GENERIC_MENU = generic_menu()


def build_snapshot(
    *,
    id: int = 12,
    author_account_id: int = 7,
    kind: VoteKind = VoteKind.BUILD,
    status: VoteStatus = VoteStatus.OPEN,
    result: VoteSessionResult = VoteSessionResult.PENDING,
    pass_threshold: int | None = 3,
    fail_threshold: int | None = -3,
    votes: Mapping[int, float] | None = None,
    messages: tuple[VoteMessage, ...] = (VoteMessage(100, 200, 10),),
    menu: VoteMenu = DEFAULT_VOTE_MENU,
    target: VoteTarget | object = _INFER_TARGET,
    selections: tuple[VoteSelection, ...] = (),
    poll: GenericPoll | None = None,
) -> VoteSessionSnapshot:
    """Build a threshold-closing snapshot, defaulting to an open build review.

    Infer the delete-log target for DELETE_LOG and the build target otherwise. Pass an
    explicit target to test invalid combinations; use poll_snapshot for generic polls.
    """
    if target is _INFER_TARGET:
        target = DEFAULT_DELETE_LOG_TARGET if kind is VoteKind.DELETE_LOG else DEFAULT_BUILD_TARGET
    return VoteSessionSnapshot(
        id=id,
        author_account_id=author_account_id,
        kind=kind,
        status=status,
        result=result,
        pass_threshold=pass_threshold,
        fail_threshold=fail_threshold,
        votes={Voter.account(account_id): weight for account_id, weight in (votes or {}).items()},
        messages=messages,
        menu=menu,
        target=cast(VoteTarget, target),
        selections=selections,
        poll=poll,
    )


def poll_snapshot(
    *,
    id: int = 1,
    author_account_id: int = 2,
    status: VoteStatus = VoteStatus.OPEN,
    result: VoteSessionResult = VoteSessionResult.PENDING,
    visibility: VoteVisibility = VoteVisibility.ANONYMOUS_LIVE,
    question: str = "Question?",
    guild_id: int | None = 10,
    scope: PollScope = PollScope.GUILD,
    messages: tuple[VoteMessage, ...] = (),
    menu: VoteMenu = GENERIC_MENU,
    selections: tuple[VoteSelection, ...] = (),
    deadline: Instant | None = None,
) -> VoteSessionSnapshot:
    """Build a generic poll snapshot, which never carries thresholds."""
    return VoteSessionSnapshot(
        id=id,
        author_account_id=author_account_id,
        kind=VoteKind.GENERIC,
        status=status,
        result=result,
        pass_threshold=None,
        fail_threshold=None,
        votes={},
        messages=messages,
        menu=menu,
        target=None,
        selections=selections,
        poll=GenericPoll(question, visibility, deadline or Instant.now().add(hours=1), guild_id, scope),
    )


async def attach_vote_message(
    session: AsyncSession,
    *,
    message_id: int,
    vote_session_id: int,
    content: str | None = None,
    guild_id: int = GUILD_ID,
    channel_id: int = CHANNEL_ID,
) -> None:
    """Register the Discord message a vote session is rendered on.

    Insert both the message record and its discord_posts link so repositories can resolve
    the session from the message. The caller owns the transaction and commit.
    """
    await record_vote_message(
        session,
        message_id=message_id,
        content=content,
        guild_id=guild_id,
        channel_id=channel_id,
    )
    await session.execute(
        insert(DiscordPost).values(
            message_id=message_id,
            channel_id=channel_id,
            resource_kind="vote_session",
            resource_key=str(vote_session_id),
            surface="vote_card",
            applied_revision=0,
        )
    )


async def record_vote_message(
    session: AsyncSession,
    *,
    message_id: int,
    content: str | None = None,
    guild_id: int = GUILD_ID,
    channel_id: int = CHANNEL_ID,
) -> None:
    """Record the Discord message that the attachment operation consumes."""
    await session.execute(
        insert(Message).values(
            id=message_id,
            guild_id=guild_id,
            channel_id=channel_id,
            author_id=AUTHOR_ACCOUNT_IDS[0],
            content=content,
        )
    )


async def seed_delete_log_vote(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    pass_threshold: int = 3,
    fail_threshold: int = -3,
    votes: Mapping[int, float] | None = None,
    menu: VoteMenu = DEFAULT_VOTE_MENU,
) -> tuple[int, int]:
    """Persist an open delete-log vote session and return its session/message IDs.

    Insert mapped rows directly so repository read tests do not depend on repository writes.
    Prerequisite account and target-message rows must already exist. Signed fixture weights
    choose approve/deny options; stored weights use their positive magnitude.
    """
    async with session_factory.begin() as session:
        vote_session_id = (
            await session.execute(
                insert(VoteSession)
                .values(
                    status=VoteStatus.OPEN,
                    result=VoteSessionResult.PENDING,
                    author_account_id=AUTHOR_ACCOUNT_IDS[0],
                    kind=VoteKind.DELETE_LOG,
                    pass_threshold=pass_threshold,
                    fail_threshold=fail_threshold,
                )
                .returning(VoteSession.id)
            )
        ).scalar_one()
        message_id = 1_000 + vote_session_id
        # Projected from the domain defaults rather than restated, so that the
        # `snapshot.menu == DEFAULT_VOTE_MENU` assertions prove a round trip
        # instead of comparing two hand-written copies of the same menu.
        await session.execute(
            insert(VoteSessionOption),
            [
                {
                    "vote_session_id": vote_session_id,
                    "identifier": option.id,
                    "choice": option.choice.value,
                    "label": option.label,
                    "multiplier": option.multiplier,
                    "position": option.position,
                }
                for option in menu.options
            ],
        )
        await session.execute(
            insert(VoteSessionOptionAlias),
            [
                {
                    "vote_session_id": vote_session_id,
                    "option_identifier": alias.option_id,
                    "guild_id": alias.guild_id,
                    "emoji": alias.emoji,
                    "position": alias.position,
                }
                for alias in menu.aliases
            ],
        )
        await session.execute(
            insert(DeleteLogVoteSession).values(
                vote_session_id=vote_session_id,
                target_message_id=TARGET_MESSAGE_ID,
                target_channel_id=CHANNEL_ID,
                target_server_id=GUILD_ID,
            )
        )
        await attach_vote_message(session, message_id=message_id, vote_session_id=vote_session_id, content="Vote now")
        if votes:
            await session.execute(
                insert(Vote),
                [
                    {
                        "vote_session_id": vote_session_id,
                        "account_id": account_id,
                        "guild_id": GUILD_ID,
                        "option_identifier": "deny" if weight < 0 else "approve",
                        "emoji": "👎" if weight < 0 else "👍",
                        "weight": abs(weight),
                    }
                    for account_id, weight in votes.items()
                ],
            )
    return vote_session_id, message_id


class VoteUnitOfWork[RepositoryT](PublishingFakeUnitOfWork[RepositoryT]):
    """Publish replaced repository attributes on commit.

    The copy is shallow: in-place changes to nested objects also change the fixture before
    commit. Tests using this scope must not rely on rollback of those changes.
    """

    def __init__(self, repository: RepositoryT, *, grants: Sequence[PermissionGrant] = ()) -> None:
        self._grants = grants
        super().__init__(repository, clone=copy)

    @property
    def permissions(self) -> Self:
        return self

    async def load_for_subject(self, subject: PermissionSubject) -> SubjectRecords:
        return SubjectRecords(
            1,
            subject,
            grants=tuple(
                grant
                for grant in self._grants
                if (
                    isinstance(subject, UserActor)
                    and grant.subject_account_id is not None
                    and grant.subject_account_id == subject.account_id
                )
                or (grant.subject_guild_id == subject.guild_id and grant.subject_role_id in subject.discord_role_ids)
            ),
        )

    async def epoch(self) -> int:
        return 1

    @property
    def votes(self) -> RepositoryT:
        return self.state
