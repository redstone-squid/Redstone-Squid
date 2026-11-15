"""Roster source groups retain slot context when their member lines continue."""

from dataclasses import dataclass

from squid_ui.authoring.rosters import RosterSlot
from squid_ui.authoring.semantic import List


@dataclass(frozen=True, slots=True)
class RosterSection:
    slot: RosterSlot | None
    count: int
    full: bool = False


@dataclass(frozen=True, slots=True)
class RosterLabel:
    section: RosterSection
    full: bool = False


@dataclass(frozen=True, slots=True)
class RosterMembers(List):
    """Member lines use their enclosing region instead of introducing a local pager."""
