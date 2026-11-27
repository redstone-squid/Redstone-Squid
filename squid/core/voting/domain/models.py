"""Describe voting sessions and the ballots that determine their outcomes.

A voting session is one build review, deletion vote, or generic poll. It may appear
in several Discord messages, all sharing one set of ballots. Each account has at
most one ballot in that voting session, identified by a stable option ID rather
than by the emoji used to select it.

A ballot's weight is a positive magnitude. Build and deletion votes turn it into
a signed score: approve adds weight and deny subtracts it. They close when the net
score reaches a threshold. Generic polls total weights by option and close on a
deadline or an authorized request instead.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite

from whenever import Instant

from squid.core.actors import Actor, UserActor
from squid.core.errors import JSONValue
from squid.core.permissions.domain.catalogue import VOTE_POLL_CLOSE_ANY, VOTE_POLL_RECOUNT
from squid.core.scores import round_score
from squid.core.voting.errors import InvalidVoteConfigurationError

type VoteChange = tuple[str, object, object]


class VoteKind(StrEnum):
    """What a vote session decides, and therefore how it closes."""

    BUILD = "build"
    BUILD_EDIT = "build_edit"
    DELETE_LOG = "delete_log"
    GENERIC = "generic"

    @property
    def is_threshold_vote(self) -> bool:
        """Whether this kind closes itself once a signed net score is reached."""
        return self is not VoteKind.GENERIC


class VoteStatus(StrEnum):
    """Whether a session still accepts ballots."""

    OPEN = "open"
    COUNTING = "counting"
    REVIEW = "review"
    VOID = "void"
    CLOSED = "closed"


class ReviewKind(StrEnum):
    """A vote that reviews a build: its submission or an edit to it."""

    BUILD = VoteKind.BUILD.value
    BUILD_EDIT = VoteKind.BUILD_EDIT.value

    @property
    def vote_kind(self) -> VoteKind:
        return VoteKind(self.value)


class ReviewSort(StrEnum):
    """Review listing orders; session IDs follow creation, so both sort by ID."""

    NEWEST = "newest"
    OLDEST = "oldest"


@dataclass(frozen=True, slots=True)
class ReviewFilters:
    """Narrow a review listing; empty `kinds` means every review kind."""

    kinds: frozenset[ReviewKind] = frozenset()
    status: VoteStatus | None = None

    @property
    def vote_kinds(self) -> frozenset[VoteKind]:
        return frozenset(kind.vote_kind for kind in (self.kinds or ReviewKind))


class VoteSessionResult(StrEnum):
    """A session's outcome, or PENDING while it remains undecided."""

    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"
    PENDING = "pending"


class VoteVisibility(StrEnum):
    """How much of a generic poll's state is disclosed while it is open."""

    ANONYMOUS_LIVE = "anonymous_live"
    VISIBLE_LIVE = "visible_live"
    ANONYMOUS_HIDDEN = "anonymous_hidden"


class PollScope(StrEnum):
    """How far a generic poll is published."""

    GUILD = "guild"
    NETWORK = "network"


class VoteRejection(StrEnum):
    """Why a ballot or closure request was refused."""

    NOT_FOUND = "not_found"
    CLOSED = "closed"
    NOT_ELIGIBLE = "not_eligible"
    INVALID_OPTION = "invalid_option"
    WRONG_GUILD = "wrong_guild"
    NOT_AUTHORIZED = "not_authorized"


class BallotChange(StrEnum):
    """What happened to a ballot: a voter chose an option, withdrew it, or staff voided it."""

    CAST = "cast"
    CLEAR = "clear"
    VOID = "void"


class VoteChoice(StrEnum):
    """The scoring behavior of a session option."""

    APPROVE = "approve"
    DENY = "deny"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class VoteOption:
    """One choice a voter can pick, however a frontend shows it.

    `multiplier` scales the voter's weight, so a session can offer both a small and
    a big approve, such as `approve` at 1x and `strong_approve` at 2x.
    """

    id: str
    choice: VoteChoice
    label: str | None = None
    multiplier: float = 1.0
    position: int = 0

    def __post_init__(self) -> None:
        if not self.id.strip():
            msg = "Vote option identifier cannot be empty."
            raise InvalidVoteConfigurationError(msg)
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = "Vote option multiplier must be finite and greater than zero."
            raise InvalidVoteConfigurationError(msg, context={"multiplier": self.multiplier})
        if self.choice is VoteChoice.GENERIC and not (self.label or "").strip():
            msg = "Generic vote options require a label."
            raise InvalidVoteConfigurationError(msg)


@dataclass(frozen=True, slots=True)
class VoteAlias:
    """An emoji that selects an option on the Discord cards of one guild.

    `guild_id` None defines an alias for every guild without aliases of its own.
    Several aliases may select one option: the default 👍 and ✅ both select
    `approve`, and choosing either after the other keeps the same ballot.
    """

    option_id: str
    emoji: str
    guild_id: int | None = None
    position: int = 0

    def __post_init__(self) -> None:
        if not self.emoji.strip():
            msg = "Vote option emoji cannot be empty."
            raise InvalidVoteConfigurationError(msg)


@dataclass(frozen=True, slots=True)
class VoteMenu:
    """The options a session offers and the emoji aliases that select them on Discord."""

    options: tuple[VoteOption, ...]
    aliases: tuple[VoteAlias, ...] = ()

    @classmethod
    def of(cls, rows: Iterable[tuple[VoteOption, VoteAlias]]) -> VoteMenu:
        """Build a menu from emoji rows, each paired with the option it selects.

        The first row naming an option defines it. Positions follow row order.
        """
        options: dict[str, VoteOption] = {}
        aliases: list[VoteAlias] = []
        positions: dict[int | None, int] = {}
        for option, alias in rows:
            options.setdefault(option.id, replace(option, position=len(options)))
            position = positions[alias.guild_id] = positions.get(alias.guild_id, -1) + 1
            aliases.append(replace(alias, option_id=option.id, position=position))
        return cls(tuple(options.values()), tuple(aliases))

    def rows(self) -> tuple[tuple[VoteOption, VoteAlias], ...]:
        """Return each alias paired with its option, the inverse of `of`."""
        options = {option.id: option for option in self.options}
        return tuple((options[alias.option_id], alias) for alias in self.aliases)

    @classmethod
    def shared(cls, menus: Sequence[VoteMenu]) -> VoteMenu:
        """Combine guild menus for one session shown in all of their guilds.

        Earlier menus win an option ID, so pass the owner guild's menu first. Options
        that only other guilds define get multiplier 1: a guild hosting a card must
        not be able to weight a shared outcome.
        """
        options: dict[str, VoteOption] = {}
        for index, menu in enumerate(menus):
            for option in menu.options:
                if option.id not in options:
                    options[option.id] = option if index == 0 else replace(option, multiplier=1.0)
        return cls(tuple(options.values()), tuple(alias for menu in menus for alias in menu.aliases))

    def validated(self, kind: VoteKind) -> VoteMenu:
        """Return this menu after checking it suits `kind`.

        Option IDs and each guild's emojis must be unique, and every alias must select
        an option. Generic polls take 2 to 10 generic options. Threshold votes take
        approve and deny options, and every guild with aliases needs both choices.
        """
        if not self.options:
            msg = "Vote options cannot be empty."
            raise InvalidVoteConfigurationError(msg)
        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            msg = "Vote option identifiers must be unique."
            raise InvalidVoteConfigurationError(msg)
        aliases = [(alias.guild_id, alias.emoji) for alias in self.aliases]
        if len(aliases) != len(set(aliases)):
            msg = "Vote option emojis must be unique within a guild session."
            raise InvalidVoteConfigurationError(msg)
        choices = {option.id: option.choice for option in self.options}
        if any(alias.option_id not in choices for alias in self.aliases):
            msg = "Every vote emoji must select one of the session's options."
            raise InvalidVoteConfigurationError(msg)
        if kind is VoteKind.GENERIC:
            if not 2 <= len(self.options) <= 10:
                msg = "Generic polls require between 2 and 10 options."
                raise InvalidVoteConfigurationError(msg)
            if any(option.choice is not VoteChoice.GENERIC for option in self.options):
                msg = "Generic poll options must use the generic choice type."
                raise InvalidVoteConfigurationError(msg)
        else:
            if set(choices.values()) != {VoteChoice.APPROVE, VoteChoice.DENY}:
                msg = "Vote sessions require at least one approve and one deny option."
                raise InvalidVoteConfigurationError(msg)
            for guild_id in {alias.guild_id for alias in self.aliases}:
                scoped = {choices[alias.option_id] for alias in self.aliases if alias.guild_id == guild_id}
                if scoped != {VoteChoice.APPROVE, VoteChoice.DENY}:
                    msg = "Vote sessions require at least one approve and one deny option."
                    raise InvalidVoteConfigurationError(msg)
        return self

    def option(self, option_id: str) -> VoteOption | None:
        return next((option for option in self.options if option.id == option_id), None)

    def aliases_for_guild(self, guild_id: int) -> tuple[VoteAlias, ...]:
        """Return the guild's own aliases, falling back to the shared ones only when it has none."""
        scoped = tuple(alias for alias in self.aliases if alias.guild_id == guild_id)
        return scoped or tuple(alias for alias in self.aliases if alias.guild_id is None)

    def option_for_emoji(self, emoji: str, guild_id: int) -> VoteOption | None:
        alias = next((alias for alias in self.aliases_for_guild(guild_id) if alias.emoji == emoji), None)
        return None if alias is None else self.option(alias.option_id)

    def alias_for(self, option_id: str, guild_id: int, *, emoji: str | None = None) -> VoteAlias | None:
        """Return the guild's alias for an option: the one using `emoji` if given, otherwise the first."""
        return next(
            (
                alias
                for alias in self.aliases_for_guild(guild_id)
                if alias.option_id == option_id and (emoji is None or alias.emoji == emoji)
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class VoteSelection:
    """One account's ballot, with the positive weight it was cast with.

    `guild_id` and `emoji` record the card and alias it was cast with, or None for a
    ballot cast without Discord. The session's owner guild decides weight separately.
    """

    account_id: int
    guild_id: int | None
    option_id: str
    emoji: str | None
    weight: float
    display_name: str | None = None
    """The voter's public profile name, or None if they have none or hide it."""

    def __post_init__(self) -> None:
        if not isfinite(self.weight) or self.weight <= 0:
            msg = "Vote weight must be finite and greater than zero."
            raise InvalidVoteConfigurationError(msg)


@dataclass(frozen=True, slots=True)
class BuildVoteTarget:
    """The build a review session confirms or rejects."""

    build_id: int


@dataclass(frozen=True, slots=True)
class BuildEditVoteTarget:
    """A proposed patch against the build revision shown to voters."""

    build_id: int
    revision: int
    patch: Mapping[str, JSONValue]
    changes: tuple[VoteChange, ...]
    public_suggestion: bool = False


@dataclass(frozen=True, slots=True)
class DeleteLogVoteTarget:
    """The Discord message a moderation session votes to delete."""

    message_id: int
    channel_id: int
    server_id: int


type VoteTarget = BuildVoteTarget | BuildEditVoteTarget | DeleteLogVoteTarget | None
"""What a closing session acts on. Generic polls act on nothing and carry `None`."""


@dataclass(frozen=True, slots=True)
class VoteMessage:
    """Discord message location required to restore a vote view."""

    id: int
    channel_id: int
    guild_id: int = 0


@dataclass(frozen=True, slots=True)
class GenericPoll:
    """Question, deadline, and publication settings for a generic poll.

    `guild_id` identifies the owner guild, whose role weights govern all ballots,
    including those cast in another guild. With no owner guild, role multipliers
    are not applied. A poll can exist before any Discord message is attached.
    """

    question: str
    visibility: VoteVisibility
    deadline: Instant
    guild_id: int | None = None
    scope: PollScope = PollScope.GUILD
    voting_ended_at: Instant | None = None
    count_revision: int = 0
    count_attempts: int = 0
    retry_at: Instant | None = None
    unreadable_cards: tuple[int, ...] = ()


def validate_thresholds(kind: VoteKind, pass_threshold: int | None, fail_threshold: int | None) -> None:
    """Require positive pass and negative fail thresholds for build and deletion votes.

    Generic polls use deadlines and must have neither threshold.
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


def validate_session_payload(
    kind: VoteKind,
    target: VoteTarget,
    poll: GenericPoll | None,
    options: Sequence[VoteOption],
) -> None:
    """Enforce the discriminated target, poll, and option contract for one vote kind."""
    match kind:
        case VoteKind.BUILD:
            valid_target = isinstance(target, BuildVoteTarget)
            requires_poll = False
            valid_choices = {VoteChoice.APPROVE, VoteChoice.DENY}
        case VoteKind.BUILD_EDIT:
            valid_target = isinstance(target, BuildEditVoteTarget)
            requires_poll = False
            valid_choices = {VoteChoice.APPROVE, VoteChoice.DENY}
        case VoteKind.DELETE_LOG:
            valid_target = isinstance(target, DeleteLogVoteTarget)
            requires_poll = False
            valid_choices = {VoteChoice.APPROVE, VoteChoice.DENY}
        case VoteKind.GENERIC:
            valid_target = target is None
            requires_poll = True
            valid_choices = {VoteChoice.GENERIC}
    if not valid_target:
        msg = f"{kind.value} vote session has the wrong target payload."
        raise InvalidVoteConfigurationError(
            msg,
            context={"kind": kind.value, "target_type": type(target).__name__},
        )
    if (poll is not None) is not requires_poll:
        msg = f"{kind.value} vote session has the wrong poll payload."
        raise InvalidVoteConfigurationError(msg, context={"kind": kind.value})
    invalid_choices = sorted({option.choice.value for option in options if option.choice not in valid_choices})
    if invalid_choices:
        msg = f"{kind.value} vote session has incompatible option choices."
        raise InvalidVoteConfigurationError(
            msg,
            context={"kind": kind.value, "invalid_choices": invalid_choices},
        )


@dataclass(frozen=True, slots=True)
class VoteSessionSnapshot:
    """Validated session state used by application and presentation adapters.

    `votes` maps account IDs to signed weights for score calculations. `selections`
    retains the ballots with positive weights and stable option IDs for tallies. For example, a deny ballot of weight 2 appears as -2 in `votes`
    and as weight 2 with the default option ID `deny` in `selections`.
    """

    id: int
    author_account_id: int
    kind: VoteKind
    status: VoteStatus
    result: VoteSessionResult
    pass_threshold: int | None
    fail_threshold: int | None
    votes: Mapping[int, float]
    messages: tuple[VoteMessage, ...]
    menu: VoteMenu
    target: VoteTarget = None
    selections: tuple[VoteSelection, ...] = ()
    poll: GenericPoll | None = None

    def __post_init__(self) -> None:
        validate_thresholds(self.kind, self.pass_threshold, self.fail_threshold)
        validate_session_payload(self.kind, self.target, self.poll, self.menu.options)

    @property
    def is_open(self) -> bool:
        return self.status is VoteStatus.OPEN

    @property
    def upvotes(self) -> float:
        return round_score(sum(weight for weight in self.votes.values() if weight > 0))

    @property
    def downvotes(self) -> float:
        return round_score(-sum(weight for weight in self.votes.values() if weight < 0))

    @property
    def net_votes(self) -> float:
        return round_score(sum(self.votes.values()))

    @property
    def visibility(self) -> VoteVisibility | None:
        """The poll's disclosure mode, or None for kinds that are always anonymous."""
        return self.poll.visibility if self.poll is not None else None

    @property
    def members_only(self) -> bool:
        """Whether only members of the owning guild may vote, as in a poll kept to its guild."""
        return self.poll is not None and self.poll.scope is PollScope.GUILD and self.poll.guild_id is not None

    @property
    def is_anonymous(self) -> bool:
        """Whether voter identities are hidden; only visible live polls expose them."""
        return self.visibility is not VoteVisibility.VISIBLE_LIVE

    @property
    def shows_tallies(self) -> bool:
        """Whether current totals may be disclosed to viewers."""
        return self.visibility is not VoteVisibility.ANONYMOUS_HIDDEN or self.status is VoteStatus.CLOSED

    def should_remove_reaction_on_cast(self) -> bool:
        """Return whether the transport should remove the reaction to hide the voter's choice."""
        return self.is_anonymous

    def can_close(self, actor: Actor) -> VoteRejection | None:
        """Return why `actor` may not close this session, or None when they may."""
        if not isinstance(actor, UserActor) or self.kind is not VoteKind.GENERIC or self.poll is None:
            return VoteRejection.NOT_AUTHORIZED
        author = actor.account_id == self.author_account_id
        elsewhere = self.poll.guild_id is not None and self.poll.guild_id != actor.guild_id
        # Only the author may close a network poll from another guild. Other
        # actors must use capabilities resolved in the poll's owning guild.
        if elsewhere and not (author and self.poll.scope is PollScope.NETWORK):
            return VoteRejection.WRONG_GUILD
        if not author and VOTE_POLL_CLOSE_ANY.name not in actor.capabilities:
            return VoteRejection.NOT_AUTHORIZED
        return None

    def can_review_poll(self, actor: Actor, *, recount_only: bool) -> bool:
        if not isinstance(actor, UserActor) or self.poll is None or actor.guild_id is None:
            return False
        if self.poll.guild_id is None:
            if actor.guild_id not in {card.guild_id for card in self.messages}:
                return False
        elif actor.guild_id != self.poll.guild_id:
            return False
        return VOTE_POLL_CLOSE_ANY.name in actor.capabilities or (
            recount_only and VOTE_POLL_RECOUNT.name in actor.capabilities
        )

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
    """Result returned to the caller after a ballot or closure request.

    `just_closed` is true only when this request closed the voting session; a
    session already closed by another request does not set it.
    """

    session: VoteSessionSnapshot | None
    rejection: VoteRejection | None = None
    previous_weight: float | None = None
    current_weight: float | None = None
    just_closed: bool = False

    @property
    def accepted(self) -> bool:
        return self.rejection is None


@dataclass(frozen=True, slots=True)
class EmojiPreset:
    """The menu one guild offers by default for a voting kind."""

    guild_id: int
    kind: VoteKind
    menu: VoteMenu


@dataclass(frozen=True, slots=True)
class GuildMember:
    """A voter's roles in one guild, as a frontend last saw them."""

    discord_user_id: int
    role_ids: frozenset[int] = frozenset()
    manage_server: bool = False


@dataclass(frozen=True, slots=True)
class RoleWeight:
    """A Discord role's multiplier for one guild and voting kind."""

    guild_id: int
    kind: VoteKind
    role_id: int
    multiplier: float

    def __post_init__(self) -> None:
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = "Role weight must be finite and greater than zero."
            raise InvalidVoteConfigurationError(msg)


DEFAULT_VOTE_MENU = VoteMenu(
    (VoteOption("approve", VoteChoice.APPROVE, position=0), VoteOption("deny", VoteChoice.DENY, position=1)),
    (
        VoteAlias("approve", "👍", position=0),
        VoteAlias("approve", "✅", position=1),
        VoteAlias("deny", "👎", position=2),
        VoteAlias("deny", "❌", position=3),
    ),
)
DEFAULT_GENERIC_EMOJIS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
MIN_POLL_DURATION_SECONDS = 60
MAX_POLL_DURATION_SECONDS = 30 * 86400


@dataclass(frozen=True, slots=True)
class VoteConfiguration:
    """Stored overrides for one guild and vote kind; None means default emojis."""

    preset: EmojiPreset | None
    weights: tuple[RoleWeight, ...]
