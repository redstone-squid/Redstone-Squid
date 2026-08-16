"""Voting domain values, configuration, and outcomes."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from whenever import Instant

from squid.permissions.domain.catalogue import VOTE_POLL_CLOSE_ANY
from squid.voting.errors import InvalidVoteConfigurationError

type VoteChange = tuple[str, object, object]


class VoteKind(StrEnum):
    """What a vote session decides, and therefore how it closes."""

    BUILD = "build"
    DELETE_LOG = "delete_log"
    GENERIC = "generic"

    @property
    def is_threshold_vote(self) -> bool:
        """Whether this kind closes itself once a signed net score is reached."""
        return self is not VoteKind.GENERIC


class VoteStatus(StrEnum):
    """Whether a session still accepts ballots."""

    OPEN = "open"
    CLOSED = "closed"


class VoteSessionResult(StrEnum):
    """The decision a closed session reached."""

    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"
    PENDING = "pending"


class VoteVisibility(StrEnum):
    """How much of a generic poll's state is disclosed while it is open."""

    ANONYMOUS_LIVE = "anonymous_live"
    VISIBLE_LIVE = "visible_live"
    ANONYMOUS_HIDDEN = "anonymous_hidden"


class VoteRejection(StrEnum):
    """Why a ballot or closure request was refused."""

    NOT_FOUND = "not_found"
    CLOSED = "closed"
    NOT_ELIGIBLE = "not_eligible"
    INVALID_OPTION = "invalid_option"
    WRONG_GUILD = "wrong_guild"
    NOT_AUTHORIZED = "not_authorized"


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

    @property
    def id(self) -> str:
        """The stable identifier, independent of Discord, which `__post_init__` always fills."""
        assert self.identifier is not None
        return self.identifier


@dataclass(frozen=True, slots=True)
class VoteSelection:
    """A voter's raw selection and its last successfully calculated weight."""

    account_id: int
    guild_id: int
    option_id: str
    emoji: str
    weight: float

    def __post_init__(self) -> None:
        if not isfinite(self.weight) or self.weight <= 0:
            msg = "Cached vote weight must be finite and greater than zero."
            raise InvalidVoteConfigurationError(msg)


@dataclass(frozen=True, slots=True)
class VoteActor:
    """An account plus current Discord membership facts used to weight a ballot."""

    account_id: int
    discord_id: int
    guild_id: int = 0
    role_ids: frozenset[int] = frozenset()
    capabilities: frozenset[str] = frozenset()
    """Permission node names this actor was resolved to hold.

    Names rather than booleans, and already resolved rather than resolvable: the
    domain stays free of the permission engine, and the edge that built this
    actor is the only place that knows how to ask.
    """


@dataclass(frozen=True, slots=True)
class BuildVoteTarget:
    """The build a review session confirms or rejects."""

    build_id: int


@dataclass(frozen=True, slots=True)
class DeleteLogVoteTarget:
    """The Discord message a moderation session votes to delete."""

    message_id: int
    channel_id: int
    server_id: int


type VoteTarget = BuildVoteTarget | DeleteLogVoteTarget | None
"""What a closing session acts on. Generic polls act on nothing and carry `None`."""


@dataclass(frozen=True, slots=True)
class VoteMessage:
    """Discord message location required to restore a vote view."""

    id: int
    channel_id: int
    guild_id: int = 0


@dataclass(frozen=True, slots=True)
class GenericPoll:
    """Metadata owned by a generic poll session.

    `guild_id` is optional so a poll can exist before any presentation message is
    attached; it records the guild whose emoji aliases the poll was drafted against.
    """

    question: str
    visibility: VoteVisibility
    deadline: Instant
    guild_id: int | None = None


def validate_thresholds(kind: VoteKind, pass_threshold: int | None, fail_threshold: int | None) -> None:
    """Enforce the kind/threshold pairing the database also constrains.

    Generic polls close on a deadline and never on a score, so a threshold on one is
    not a harmless extra: it is a number nothing will ever read, which is exactly how
    the `32767`/`-32768` sentinels this replaces came to be stored.
    """
    if kind.is_threshold_vote:
        if pass_threshold is None or fail_threshold is None:
            msg = f"{kind.value} vote sessions require both thresholds."
            raise InvalidVoteConfigurationError(msg, context={"kind": kind.value})
        if pass_threshold <= 0 or fail_threshold >= 0:
            msg = "Pass thresholds must be positive and fail thresholds negative."
            raise InvalidVoteConfigurationError(
                msg, context={"pass_threshold": pass_threshold, "fail_threshold": fail_threshold}
            )
    elif pass_threshold is not None or fail_threshold is not None:
        msg = "Generic polls must not carry vote thresholds."
        raise InvalidVoteConfigurationError(msg, context={"kind": kind.value})


@dataclass(frozen=True, slots=True)
class VoteSessionSnapshot:
    """Persisted state needed by application and presentation adapters."""

    id: int
    author_account_id: int
    kind: VoteKind
    status: VoteStatus
    result: VoteSessionResult
    pass_threshold: int | None
    fail_threshold: int | None
    votes: Mapping[int, float]
    messages: tuple[VoteMessage, ...]
    options: tuple[VoteOption, ...]
    target: VoteTarget = None
    selections: tuple[VoteSelection, ...] = ()
    poll: GenericPoll | None = None

    def __post_init__(self) -> None:
        validate_thresholds(self.kind, self.pass_threshold, self.fail_threshold)

    @property
    def message_ids(self) -> tuple[int, ...]:
        return tuple(message.id for message in self.messages)

    @property
    def is_open(self) -> bool:
        return self.status is VoteStatus.OPEN

    @property
    def upvotes(self) -> float:
        return sum(weight for weight in self.votes.values() if weight > 0)

    @property
    def downvotes(self) -> float:
        return -sum(weight for weight in self.votes.values() if weight < 0)

    @property
    def net_votes(self) -> float:
        return sum(self.votes.values())

    @property
    def visibility(self) -> VoteVisibility | None:
        """The poll's disclosure mode, or None for kinds that are always anonymous."""
        return self.poll.visibility if self.poll is not None else None

    @property
    def is_anonymous(self) -> bool:
        """Whether ballots are hidden, which is every session except a live public poll."""
        return self.visibility is not VoteVisibility.VISIBLE_LIVE

    @property
    def shows_tallies(self) -> bool:
        """Whether current totals may be disclosed to viewers."""
        return self.visibility is not VoteVisibility.ANONYMOUS_HIDDEN or not self.is_open

    def should_remove_reaction_on_cast(self) -> bool:
        """Whether the transport must strip a voter's reaction to keep the ballot secret.

        A public live poll is the one case where the reaction *is* the disclosed
        ballot, so removing it would erase the very thing the voter chose to show.
        """
        return self.is_anonymous

    def can_close(self, actor: VoteActor) -> VoteRejection | None:
        """Return why `actor` may not close this session, or None when they may."""
        if self.kind is not VoteKind.GENERIC or self.poll is None:
            return VoteRejection.NOT_AUTHORIZED
        if self.poll.guild_id is not None and actor.guild_id and self.poll.guild_id != actor.guild_id:
            return VoteRejection.WRONG_GUILD
        if actor.account_id != self.author_account_id and VOTE_POLL_CLOSE_ANY.name not in actor.capabilities:
            return VoteRejection.NOT_AUTHORIZED
        return None

    def options_for_guild(self, guild_id: int) -> tuple[VoteOption, ...]:
        """Return the snapshotted aliases applicable to a guild message."""
        scoped = tuple(option for option in self.options if option.guild_id == guild_id)
        return scoped or tuple(option for option in self.options if option.guild_id is None)

    def option_by_emoji(self, emoji: str, guild_id: int) -> VoteOption | None:
        return next((option for option in self.options_for_guild(guild_id) if option.emoji == emoji), None)

    def option_by_id(self, option_id: str, guild_id: int) -> VoteOption | None:
        return next((option for option in self.options_for_guild(guild_id) if option.id == option_id), None)

    def selection_for(self, account_id: int) -> VoteSelection | None:
        return next((selection for selection in self.selections if selection.account_id == account_id), None)

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
    unresolved_account_ids: tuple[int, ...] = ()
    just_closed: bool = False

    @property
    def complete(self) -> bool:
        return not self.unresolved_account_ids


@dataclass(frozen=True, slots=True)
class EmojiPreset:
    """An ordered guild/session-kind emoji configuration."""

    guild_id: int
    kind: VoteKind
    options: tuple[VoteOption, ...]


@dataclass(frozen=True, slots=True)
class RoleWeight:
    """A guild/session-scoped role multiplier."""

    guild_id: int
    kind: VoteKind
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
MIN_POLL_DURATION_SECONDS = 60
MAX_POLL_DURATION_SECONDS = 30 * 86400


def normalize_vote_options(options: Sequence[VoteOption], *, kind: VoteKind = VoteKind.BUILD) -> tuple[VoteOption, ...]:
    """Validate and freeze snapshotted options."""
    normalized = tuple(options)
    if not normalized:
        msg = "Vote options cannot be empty."
        raise InvalidVoteConfigurationError(msg)
    aliases = [(option.guild_id, option.emoji) for option in normalized]
    if len(aliases) != len(set(aliases)):
        msg = "Vote option emojis must be unique within a guild session."
        raise InvalidVoteConfigurationError(msg)
    if kind is VoteKind.GENERIC:
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
