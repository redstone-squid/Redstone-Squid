"""Immutable display values for host-owned tallies."""

from dataclasses import dataclass

from squid_layouts.text import TextLike


@dataclass(frozen=True, slots=True)
class TallyOption:
    """One option and its host-computed count."""

    key: str
    label: TextLike
    count: int
    mine: bool = False
    emoji: str | None = None

    def __post_init__(self) -> None:
        if not self.key:
            message = "tally option key must not be empty"
            raise ValueError(message)
        if self.count < 0:
            message = "tally option count must not be negative"
            raise ValueError(message)


__all__ = ["TallyOption"]
