"""Immutable display values for host-owned tallies.

Named in the plural for the same reason as `grids`: `factories.tally` is the factory.
"""

from dataclasses import dataclass

from squid_ui.text import TextLike


@dataclass(frozen=True, slots=True)
class TallyOption:
    """One option and its host-computed count; raises `ValueError` for an empty `key` or a negative `count`."""

    key: str
    label: TextLike
    count: int
    mine: bool = False
    """The viewer voted for this option: drawn bold and preselected in the vote control."""
    emoji: str | None = None

    def __post_init__(self) -> None:
        if not self.key:
            message = "tally option key must not be empty"
            raise ValueError(message)
        if self.count < 0:
            message = "tally option count must not be negative"
            raise ValueError(message)


__all__ = ["TallyOption"]
