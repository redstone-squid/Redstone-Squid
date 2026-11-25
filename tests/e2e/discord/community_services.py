"""In-memory application data for community command journeys."""

from collections.abc import Mapping, Sequence
from typing import override

from whenever import Instant

from squid.core.actors import ActingContext
from squid.core.diagnostics.application import ErrorReportService
from squid.core.errors import JSONValue
from squid.core.events.application import DomainEventDelivery, DomainEventService
from squid.core.messages.application import MessageService
from squid.core.messages.domain import MessageSnapshot
from squid.core.posts.application import PostService
from squid.core.posts.domain import ResourceKind, Surface
from squid.core.starboard.application import StarboardService
from squid.core.starboard.application.snapshots import StarboardSnapshot
from squid.core.starboard.models import StarboardConfig
from squid.core.tags.application import TagService
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
from squid.core.voting.application import VoteService
from squid.core.voting.application.ports import VoteActorResolver
from squid.core.voting.domain import (
    DEFAULT_VOTE_OPTIONS,
    EmojiPreset,
    PollScope,
    VoteKind,
    VoteOption,
    VoteSessionSnapshot,
    VoteVisibility,
)
from squid_ui.ext.discord.testing import AsyncCallRecorder
from tests.support.settings import RuntimeSettingsStub


class CommunitySettings(RuntimeSettingsStub):
    @override
    async def get_locale(self, server_id: int, *, context: ActingContext) -> str | None:
        return None


class EmptyDomainEvents(DomainEventService):
    def __init__(self) -> None:
        pass

    @override
    async def claim(self, consumer: str, limit: int = 20, *, context: ActingContext) -> Sequence[DomainEventDelivery]:
        return ()


class CommunityVotes(VoteService):
    def __init__(self) -> None:
        self.reviews: dict[int, tuple[int, ...]] = {}
        self.cards: dict[int, VoteSessionSnapshot] = {}
        self.deletion_targets: list[tuple[int, int, int, int]] = []
        self.poll_creations = AsyncCallRecorder(result=42)
        self.poll_attachments = AsyncCallRecorder()

    @override
    async def create_generic_poll(
        self,
        *,
        context: ActingContext,
        question: str,
        visibility: VoteVisibility,
        duration_seconds: int,
        options: Sequence[VoteOption],
        guild_id: int | None = None,
        scope: PollScope = PollScope.GUILD,
        now: Instant | None = None,
    ) -> int:
        return await self.poll_creations(
            author_account_id=context.effective.require_account_id(),
            question=question,
            visibility=visibility,
            duration_seconds=duration_seconds,
            options=options,
            guild_id=guild_id,
            scope=scope,
        )

    @override
    async def attach_message(self, vote_session_id: int, message_id: int, *, context: ActingContext) -> None:
        await self.poll_attachments(vote_session_id, message_id)

    @override
    def set_actor_resolver(self, resolver: VoteActorResolver) -> None:
        self.resolver = resolver

    @override
    async def get_session_for_message(self, message_id: int, *, context: ActingContext) -> VoteSessionSnapshot | None:
        return self.cards[message_id]

    @override
    async def pending_reviews(self, guild_id: int, *, context: ActingContext) -> Sequence[int]:
        return self.reviews[guild_id]

    @override
    async def emoji_preset(self, guild_id: int, kind: VoteKind, *, context: ActingContext) -> EmojiPreset:
        return EmojiPreset(guild_id, kind, DEFAULT_VOTE_OPTIONS)

    @override
    async def start_delete_log_vote(
        self,
        *,
        pass_threshold: int,
        fail_threshold: int,
        message_id: int,
        channel_id: int,
        server_id: int,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
        context: ActingContext,
    ) -> int:
        self.deletion_targets.append((context.effective.require_account_id(), server_id, channel_id, message_id))
        return 42


class VotePosts(PostService):
    def __init__(self) -> None:
        self.cards: dict[str, tuple[int, int]] = {}

    @override
    async def record(
        self,
        *,
        message_id: int,
        channel_id: int,
        resource_kind: ResourceKind,
        resource_key: str,
        surface: Surface,
        applied_revision: int,
        context: ActingContext,
    ) -> None:
        assert resource_kind == "vote_session"
        assert surface == "vote_card"
        self.cards[resource_key] = channel_id, message_id

    @override
    async def pending_generation(
        self, resource_kind: ResourceKind, resource_key: str, *, context: ActingContext
    ) -> int | None:
        return None


class ObservedMessages(MessageService):
    def __init__(self) -> None:
        self.observed: list[MessageSnapshot] = []

    @override
    async def observe(self, snapshot: MessageSnapshot, *, context: ActingContext) -> None:
        self.observed.append(snapshot)


class Boards(StarboardService):
    def __init__(self) -> None:
        self.boards: dict[int, tuple[StarboardConfig, ...]] = {}

    @override
    async def list_for_guild(self, guild_id: int, *, context: ActingContext) -> tuple[StarboardConfig, ...]:
        return self.boards[guild_id]

    @override
    async def snapshot(self, starboard_id: int, *, context: ActingContext) -> StarboardSnapshot:
        (board,) = (board for boards in self.boards.values() for board in boards if board.id == starboard_id)
        return StarboardSnapshot.capture(board, {})


class OwnerReports(ErrorReportService):
    def __init__(self) -> None:
        self.failures: dict[str, BaseException] = {}

    @override
    async def record(
        self,
        error: BaseException,
        *,
        correlation_id: str,
        reference: str,
        surface: str,
        origin: str | None = None,
        context: ActingContext,
        details: Mapping[str, JSONValue] | None = None,
        log_tail: Sequence[str] = (),
        work_lost: bool = False,
    ) -> None:
        self.failures[reference] = error


class PublishedTags(TagService):
    def __init__(self) -> None:
        pass

    @override
    async def public_definitions(self, *, context: ActingContext) -> Sequence[TagDefinition]:
        return (
            TagDefinition(
                id=4,
                stable_key="hip-door",
                display_name="Hip door",
                authority=TagAuthority.USER,
                semantic_kind=TagSemanticKind.SHOWCASE,
                value_type=TagValueType.NONE,
                moderation_status=TagModerationStatus.APPROVED,
            ),
        )
