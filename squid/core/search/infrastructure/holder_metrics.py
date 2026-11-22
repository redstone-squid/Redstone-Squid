"""Interpret the measurements stored in a record holder snapshot."""

from collections.abc import Iterator

from squid.core.records.domain import BuildKind
from squid.core.records.domain.models import DOOR_TIMING_METHODS, EXTENDER_TIMING_METHODS, TimingMethod

_TIMING_METHODS = {
    BuildKind.DOOR.value: DOOR_TIMING_METHODS,
    BuildKind.EXTENDER.value: EXTENDER_TIMING_METHODS,
}


def timing_values(build_kind: str, timing: object) -> Iterator[tuple[str, int]]:
    """Yield named, known timing values in game ticks."""
    if isinstance(timing, list):
        for method, value in zip(_TIMING_METHODS.get(build_kind, ()), timing, strict=False):
            if type(value) is int:
                name = {
                    TimingMethod.OPENING_VISIBLE: "visible_opening_time",
                    TimingMethod.CLOSING_VISIBLE: "visible_closing_time",
                }.get(method, f"{method}_time")
                yield name, value
