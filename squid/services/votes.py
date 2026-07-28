"""Application service for reaction-based voting."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Literal, Protocol

from squid.db.schema import VoteKindLiteral, VoteSessionResultLiteral

VoteStatus = Literal["open", "closed"]
VoteRejection = Literal["not_found", "closed", "not_eligible", "invalid_option"]
type VoteChange = tuple[str, object, object]


class VoteChoice(StrEnum):
    """A choice available to a voter."""

    APPROVE = "approve"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class VoteOption:
    """A reaction option and its contribution to a vote."""

    emoji: str
    choice: VoteChoice
    multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not self.emoji:
            msg = "Vote option emoji cannot be empty."
            raise ValueError(msg)
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = "Vote option multiplier must be finite and greater than zero."
            raise ValueError(msg)


DEFAULT_VOTE_OPTIONS = (
    VoteOption("👍", VoteChoice.APPROVE),
    VoteOption("✅", VoteChoice.APPROVE),
    VoteOption("👎", VoteChoice.DENY),
    VoteOption("❌", VoteChoice.DENY),
)


def normalize_vote_options(options: Sequence[VoteOption]) -> tuple[VoteOption, ...]:
    """Validate and freeze the options for a vote session."""
    normalized = tuple(options)
    emojis = [option.emoji for option in normalized]
    if len(emojis) != len(set(emojis)):
        msg = "Vote option emojis must be unique within a session."
        raise ValueError(msg)
    choices = {option.choice for option in normalized}
    if choices != {VoteChoice.APPROVE, VoteChoice.DENY}:
        msg = "Vote sessions require at least one approve and one deny option."
        raise ValueError(msg)
    return normalized


@dataclass(frozen=True, slots=True)
class VoteActor:
    """Framework-neutral facts used to authorize and weight a vote."""

    user_id: int
    is_staff: bool
    is_trusted: bool


@dataclass(frozen=True, slots=True)
class VoteTarget:
    """The application object affected when a vote closes."""

    build_id: int | None = None
    message_id: int | None = None
    channel_id: int | None = None
    server_id: int | None = None


@dataclass(frozen=True, slots=True)
class VoteSessionSnapshot:
    """Persisted state needed by the application and presentation adapters."""

    id: int
    kind: VoteKindLiteral
    status: VoteStatus
    result: VoteSessionResultLiteral
    pass_threshold: int
    fail_threshold: int
    votes: Mapping[int, float]
    message_ids: tuple[int, ...]
    options: tuple[VoteOption, ...]
    target: VoteTarget

    @property
    def upvotes(self) -> float:
        return sum(weight for weight in self.votes.values() if weight > 0)

    @property
    def downvotes(self) -> float:
        return -sum(weight for weight in self.votes.values() if weight < 0)

    @property
    def net_votes(self) -> float:
        return sum(self.votes.values())


@dataclass(frozen=True, slots=True)
class StoredVoteMutation:
    """Result of the repository's atomic vote mutation."""

    session: VoteSessionSnapshot
    previous_weight: float | None
    current_weight: float | None
    just_closed: bool


@dataclass(frozen=True, slots=True)
class CastVoteResult:
    """Outcome returned to a reaction adapter."""

    session: VoteSessionSnapshot | None
    rejection: VoteRejection | None = None
    previous_weight: float | None = None
    current_weight: float | None = None
    just_closed: bool = False

    @property
    def accepted(self) -> bool:
        return self.rejection is None


class VoteRepository(Protocol):
    """Persistence operations required by :class:`VoteService`."""

    async def create_build_session(
        self,
        *,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption],
    ) -> int: ...

    async def create_delete_log_session(
        self,
        *,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        message_id: int,
        channel_id: int,
        server_id: int,
        options: Sequence[VoteOption],
    ) -> int: ...

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None: ...

    async def cast_vote(
        self,
        message_id: int,
        user_id: int,
        desired_weight: float,
    ) -> StoredVoteMutation | None: ...


class VoteService:
    """Own voting authorization, weights, choices, toggling, and closure policy."""

    def __init__(self, repository: VoteRepository):
        self._repository = repository

    async def start_build_vote(
        self,
        *,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> int:
        """Create a build vote and its target atomically."""
        options = normalize_vote_options(options)
        return await self._repository.create_build_session(
            author_id=author_id,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            build_id=build_id,
            changes=changes,
            options=options,
        )

    async def start_delete_log_vote(
        self,
        *,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        message_id: int,
        channel_id: int,
        server_id: int,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> int:
        """Create a message-deletion vote and its target atomically."""
        options = normalize_vote_options(options)
        return await self._repository.create_delete_log_session(
            author_id=author_id,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            message_id=message_id,
            channel_id=channel_id,
            server_id=server_id,
            options=options,
        )

    async def get_session(self, message_id: int) -> VoteSessionSnapshot | None:
        return await self._repository.get_by_message(message_id)

    async def cast_vote(self, message_id: int, actor: VoteActor, emoji: str) -> CastVoteResult:
        snapshot = await self._repository.get_by_message(message_id)
        if snapshot is None:
            return CastVoteResult(session=None, rejection="not_found")
        if snapshot.status != "open":
            return CastVoteResult(session=snapshot, rejection="closed")
        if snapshot.kind == "delete_log" and not (actor.is_trusted or actor.is_staff):
            return CastVoteResult(session=snapshot, rejection="not_eligible")

        option = next((option for option in snapshot.options if option.emoji == emoji), None)
        if option is None:
            return CastVoteResult(session=snapshot, rejection="invalid_option")

        weight = option.multiplier * (3.0 if actor.is_staff else 1.0)
        desired_weight = weight if option.choice is VoteChoice.APPROVE else -weight
        mutation = await self._repository.cast_vote(message_id, actor.user_id, desired_weight)
        if mutation is None:
            latest = await self._repository.get_by_message(message_id)
            rejection: VoteRejection = "closed" if latest is not None else "not_found"
            return CastVoteResult(session=latest, rejection=rejection)

        return CastVoteResult(
            session=mutation.session,
            previous_weight=mutation.previous_weight,
            current_weight=mutation.current_weight,
            just_closed=mutation.just_closed,
        )
