"""Voting domain values and outcomes."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Literal, TypeAlias

from squid.voting.errors import InvalidVoteConfigurationError

VoteKindLiteral = Literal["build", "delete_log"]
VoteSessionResultLiteral: TypeAlias = Literal["approved", "denied", "cancelled", "pending"]
VoteChoiceLiteral: TypeAlias = Literal["approve", "deny"]
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
            raise InvalidVoteConfigurationError(msg)
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = "Vote option multiplier must be finite and greater than zero."
            raise InvalidVoteConfigurationError(msg, context={"multiplier": self.multiplier})


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
class VoteMessage:
    """Discord message location required to restore a vote view."""

    id: int
    channel_id: int


@dataclass(frozen=True, slots=True)
class VoteSessionSnapshot:
    """Persisted state needed by the application and presentation adapters."""

    id: int
    author_id: int
    kind: VoteKindLiteral
    status: VoteStatus
    result: VoteSessionResultLiteral
    pass_threshold: int
    fail_threshold: int
    votes: Mapping[int, float]
    messages: tuple[VoteMessage, ...]
    options: tuple[VoteOption, ...]
    target: VoteTarget

    @property
    def message_ids(self) -> tuple[int, ...]:
        return tuple(message.id for message in self.messages)

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
        raise InvalidVoteConfigurationError(msg)
    choices = {option.choice for option in normalized}
    if choices != {VoteChoice.APPROVE, VoteChoice.DENY}:
        msg = "Vote sessions require at least one approve and one deny option."
        raise InvalidVoteConfigurationError(msg)
    return normalized
