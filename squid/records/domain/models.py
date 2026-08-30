"""Value objects used by record calculators."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from squid.core.errors import ValidationError
from squid.core.i18n import tr


class RecordClass(StrEnum):
    """A metric by which records are classified."""

    FIRST = "first"
    FASTEST = "fastest"
    SMALLEST = "smallest"
    FASTEST_SMALLEST = "fastest_smallest"
    SMALLEST_FASTEST = "smallest_fastest"


class BuildKind(StrEnum):
    """A kind of redstone build recognized by the rules."""

    DOOR = "door"
    ENTRANCE = "entrance"
    EXTENDER = "extender"
    UTILITY = "utility"


class VersionScope(StrEnum):
    """The version population in which builds compete."""

    ALL_TIME = "all_time"
    CURRENT = "current"


class ResolutionStatus(StrEnum):
    """Whether a competition produced an official result."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    NO_CANDIDATE = "no_candidate"


class TimingMethod(StrEnum):
    """Timing methods ordered by each build kind's rules."""

    OPENING = "opening"
    OPENING_VISIBLE = "opening_visible"
    CLOSING = "closing"
    CLOSING_VISIBLE = "closing_visible"
    OPENING_RESET = "opening_reset"
    CLOSING_RESET = "closing_reset"
    RETRACTION = "retraction"
    EXTENSION = "extension"
    RETRACTION_RESET = "retraction_reset"
    EXTENSION_RESET = "extension_reset"


DOOR_TIMING_METHODS: tuple[TimingMethod, ...] = (
    TimingMethod.OPENING,
    TimingMethod.OPENING_VISIBLE,
    TimingMethod.CLOSING,
    TimingMethod.CLOSING_VISIBLE,
    TimingMethod.OPENING_RESET,
    TimingMethod.CLOSING_RESET,
)

EXTENDER_TIMING_METHODS: tuple[TimingMethod, ...] = (
    TimingMethod.RETRACTION,
    TimingMethod.EXTENSION,
    TimingMethod.RETRACTION_RESET,
    TimingMethod.EXTENSION_RESET,
)


@dataclass(frozen=True, slots=True)
class TimingVariant:
    """One possible timing behavior, represented in game ticks."""

    values: tuple[int | None, ...]

    def __post_init__(self) -> None:
        if not self.values:
            msg = tr(t"A timing variant must contain at least its primary timing method.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class CandidateGap:
    """A fact missing precisely where it prevents a result."""

    build_id: int
    field: str


@dataclass(frozen=True, slots=True)
class RecordCandidate:
    """The fixed-volume and timing facts for one competing build."""

    build_id: int
    completion_at: datetime | None = None
    fixed_volume: int | None = None
    timing_variants: tuple[TimingVariant, ...] = ()

    def __post_init__(self) -> None:
        if self.fixed_volume is not None and self.fixed_volume <= 0:
            msg = tr(t"Fixed volume must be positive.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class TimingReduction:
    """A build's lexicographically slowest known timing behavior."""

    timing: TimingVariant | None
    missing_index: int | None = None


@dataclass(frozen=True, slots=True)
class RecordResolution:
    """The official holders or the decisive gaps for a competition."""

    status: ResolutionStatus
    holder_ids: tuple[int, ...] = ()
    provisional_holder_ids: tuple[int, ...] = ()
    gaps: tuple[CandidateGap, ...] = ()

    def __post_init__(self) -> None:
        if self.status is ResolutionStatus.RESOLVED and not self.holder_ids:
            msg = tr(t"A resolved competition must have at least one holder.")
            raise ValidationError(msg)
        if self.status is not ResolutionStatus.RESOLVED and self.holder_ids:
            msg = tr(t"Only a resolved competition can have official holders.")
            raise ValidationError(msg)
