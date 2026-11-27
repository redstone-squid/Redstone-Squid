"""Public measurements and holder summaries for published records."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from squid.core.builds.application.queries import PublicBuildCreator, PublicBuildPreview, PublicBuildSummary
from squid.core.records.domain import BuildKind
from squid.core.records.domain.models import DOOR_TIMING_METHODS, EXTENDER_TIMING_METHODS, TimingMethod
from squid_i18n import DATETIME, NUMBER, tr

_TIMING_METHODS: dict[str, tuple[TimingMethod, ...]] = {
    BuildKind.DOOR: DOOR_TIMING_METHODS,
    BuildKind.EXTENDER: EXTENDER_TIMING_METHODS,
}
_TIMING_NAMES = {
    TimingMethod.OPENING_VISIBLE: "visible_opening_time",
    TimingMethod.CLOSING_VISIBLE: "visible_closing_time",
}


class MetricUnit(StrEnum):
    BLOCKS = "blocks"
    GAME_TICKS = "game_ticks"
    DATE = "date"
    """An ISO 8601 date and time."""


@dataclass(frozen=True, slots=True)
class RecordMetric:
    """A holder measurement; `display` is rendered in the locale current at construction."""

    value: int | str
    unit: MetricUnit
    display: str

    @classmethod
    def render(cls, value: int | str, unit: MetricUnit) -> Self:
        if isinstance(value, str):
            moment = datetime.fromisoformat(value)
            display = tr(t"records-metric-date = {DATETIME(moment, date_style='medium', time_zone='UTC')}")
        elif unit is MetricUnit.BLOCKS:
            display = tr(t"records-metric-blocks = {NUMBER(value)} blocks")
        else:
            display = tr(t"records-metric-game-ticks = {NUMBER(value)} gt")
        return cls(value, unit, display.resolve())


def snapshot_values(kind: str, snapshot: Mapping[str, object]) -> Iterator[tuple[str, int | str, MetricUnit]]:
    """Yield the known measurements in a stored holder snapshot.

    Timings are stored as a vector in the kind's timing-method order; unknown
    entries are skipped, as are measurements the holder's configurations disagree on.
    """
    if type(volume := snapshot.get("volume")) is int:
        yield "volume", volume, MetricUnit.BLOCKS
    if isinstance(timing := snapshot.get("timing"), list):
        for method, value in zip(_TIMING_METHODS.get(kind, ()), timing, strict=False):
            if type(value) is int:
                yield _TIMING_NAMES.get(method, f"{method}_time"), value, MetricUnit.GAME_TICKS
    if isinstance(completion := snapshot.get("completion_at"), str):
        yield "completion_at", completion, MetricUnit.DATE


def holder_metrics(kind: str, snapshot: Mapping[str, object]) -> dict[str, RecordMetric]:
    """Render a stored holder snapshot as public metrics."""
    return {name: RecordMetric.render(value, unit) for name, value, unit in snapshot_values(kind, snapshot)}


@dataclass(frozen=True, slots=True)
class RecordHolderSummary:
    """The public card facts for a build that holds or held a record."""

    build_id: int
    title: str
    creators: tuple[PublicBuildCreator, ...]
    preview: PublicBuildPreview | None

    @classmethod
    def from_build(cls, build: PublicBuildSummary) -> Self:
        return cls(build.id, build.title, build.creators, build.preview)
