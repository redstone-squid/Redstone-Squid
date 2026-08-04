"""Voting domain values, configuration, and outcomes."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Literal, TypeAlias

from whenever import Instant

from squid.reactions.domain import ReactionActor
from squid.voting.errors import InvalidVoteConfigurationError

VoteKindLiteral = Literal["build", "delete_log", "generic"]
VoteSessionResultLiteral: TypeAlias = Literal["approved", "denied", "cancelled", "pending"]
VoteChoiceLiteral: TypeAlias = Literal["approve", "deny", "generic"]
VoteStatus = Literal["open", "closed"]
VoteVisibility = Literal["anonymous_live", "visible_live", "anonymous_hidden"]
VoteRejection = Literal["not_found", "closed", "not_eligible", "invalid_option", "wrong_guild", "not_authorized"]
type VoteChange = tuple[str, object, object]


class VoteChoice(StrEnum):
    """The scoring behavior of a session option."""

    APPROVE = "approve"
    DENY = "deny"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class VoteOption:
    """A stable selectable option and one guild's reaction alias for it."""

    emoji: str
    choice: VoteChoice
    multiplier: float = 1.0
    identifier: str | None = None
    guild_id: int | None = None
    label: str | None = None
    position: int = 0

    def __post_init__(self) -> None:
        if not self.emoji.strip():
            msg = "Vote option emoji cannot be empty."
            raise InvalidVoteConfigurationError(msg)
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = "Vote option multiplier must be finite and greater than zero."
            raise InvalidVoteConfigurationError(msg, context={"multiplier": self.multiplier})
        identifier = self.identifier or (self.choice.value if self.choice is not VoteChoice.GENERIC else self.emoji)
        if not identifier.strip():
            msg = "Vote option identifier cannot be empty."
            raise InvalidVoteConfigurationError(msg)
        object.__setattr__(self, "identifier", identifier)
        if self.choice is VoteChoice.GENERIC and not (self.label or "").strip():
            msg = "Generic vote options require a label."
            raise InvalidVoteConfigurationError(msg)


@dataclass(frozen=True, slots=True)
class VoteSelection:
    """A voter's raw selection and its last successfully calculated weight."""

    user_id: int
    guild_id: int
    option_id: str
    emoji: str
    weight: float

    def __post_init__(self) -> None:
        if not isfinite(self.weight) or self.weight <= 0:
            msg = "Cached vote weight must be finite and greater than zero."
            raise InvalidVoteConfigurationError(msg)


VoteActor = ReactionActor


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
    guild_id: int = 0


@dataclass(frozen=True, slots=True)
class GenericPoll:
    """Metadata owned by a generic poll session."""

    question: str
    visibility: VoteVisibility
    guild_id: int
    deadline: Instant


@dataclass(frozen=True, slots=True)
class VoteSessionSnapshot:
    """Persisted state needed by application and presentation adapters."""

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
    selections: tuple[VoteSelection, ...] = ()
    poll: GenericPoll | None = None

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

    def options_for_guild(self, guild_id: int) -> tuple[VoteOption, ...]:
        """Return the snapshotted aliases applicable to a guild message."""
        scoped = tuple(option for option in self.options if option.guild_id == guild_id)
        return scoped or tuple(option for option in self.options if option.guild_id is None)

    def raw_tallies(self) -> Mapping[str, int]:
        """Return selection counts by stable option identifier."""
        result: defaultdict[str, int] = defaultdict(int)
        for selection in self.selections:
            result[selection.option_id] += 1
        return dict(result)

    def weighted_tallies(self) -> Mapping[str, float]:
        """Return positive weighted totals by stable option identifier."""
        result: defaultdict[str, float] = defaultdict(float)
        for selection in self.selections:
            result[selection.option_id] += selection.weight
        return dict(result)


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


@dataclass(frozen=True, slots=True)
class VoteRefreshResult:
    """Outcome of recomputing cached weights."""

    session: VoteSessionSnapshot | None
    unresolved_user_ids: tuple[int, ...] = ()
    just_closed: bool = False

    @property
    def complete(self) -> bool:
        return not self.unresolved_user_ids


@dataclass(frozen=True, slots=True)
class EmojiPreset:
    """An ordered guild/session-kind emoji configuration."""

    guild_id: int
    kind: VoteKindLiteral
    options: tuple[VoteOption, ...]


@dataclass(frozen=True, slots=True)
class RoleWeight:
    """A guild/session-scoped role multiplier."""

    guild_id: int
    kind: VoteKindLiteral
    role_id: int
    multiplier: float

    def __post_init__(self) -> None:
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = "Role weight must be finite and greater than zero."
            raise InvalidVoteConfigurationError(msg)


DEFAULT_VOTE_OPTIONS = (
    VoteOption("👍", VoteChoice.APPROVE, position=0),
    VoteOption("✅", VoteChoice.APPROVE, position=1),
    VoteOption("👎", VoteChoice.DENY, position=2),
    VoteOption("❌", VoteChoice.DENY, position=3),
)
DEFAULT_GENERIC_EMOJIS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")


def normalize_vote_options(options: Sequence[VoteOption], *, kind: VoteKindLiteral = "build") -> tuple[VoteOption, ...]:
    """Validate and freeze snapshotted options."""
    normalized = tuple(options)
    if not normalized:
        msg = "Vote options cannot be empty."
        raise InvalidVoteConfigurationError(msg)
    aliases = [(option.guild_id, option.emoji) for option in normalized]
    if len(aliases) != len(set(aliases)):
        msg = "Vote option emojis must be unique within a guild session."
        raise InvalidVoteConfigurationError(msg)
    if kind == "generic":
        identifiers = [option.identifier for option in normalized]
        if len(identifiers) != len(set(identifiers)):
            msg = "Generic option identifiers must be unique."
            raise InvalidVoteConfigurationError(msg)
        if not 2 <= len(normalized) <= 10:
            msg = "Generic polls require between 2 and 10 options."
            raise InvalidVoteConfigurationError(msg)
        if any(option.choice is not VoteChoice.GENERIC for option in normalized):
            msg = "Generic poll options must use the generic choice type."
            raise InvalidVoteConfigurationError(msg)
    else:
        for guild_id in {option.guild_id for option in normalized}:
            choices = {option.choice for option in normalized if option.guild_id == guild_id}
            if choices != {VoteChoice.APPROVE, VoteChoice.DENY}:
                msg = "Vote sessions require at least one approve and one deny option."
                raise InvalidVoteConfigurationError(msg)
    return normalized
