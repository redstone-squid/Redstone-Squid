"""Immutable roster values shared by semantic rendering and public patterns."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from squid_layouts.palette import Tone
from squid_layouts.text import TextLike


@dataclass(frozen=True, slots=True)
class RosterSlot:
    """One capacity-constrained destination in a roster."""

    key: str
    label: TextLike
    capacity: int | None = None
    tone: Tone = Tone.NEUTRAL

    def __post_init__(self) -> None:
        if not self.key:
            message = "roster slot key must not be empty"
            raise ValueError(message)
        if self.capacity is not None and self.capacity < 0:
            message = "roster slot capacity must not be negative"
            raise ValueError(message)


class RosterOverflow(StrEnum):
    """What allocation does with an entry after its requested slot fills."""

    REJECT = "reject"
    WAITLIST = "waitlist"


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """One actor's requested roster slot."""

    actor_id: str
    display: TextLike
    slot: str
    joined_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.actor_id:
            message = "roster actor id must not be empty"
            raise ValueError(message)
        if not self.slot:
            message = "roster entry slot must not be empty"
            raise ValueError(message)


class RosterStatus(StrEnum):
    """The allocation outcome for one actor."""

    SEATED = "seated"
    WAITLISTED = "waitlisted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RosterGroup:
    """One slot paired with the entries allocated to it."""

    slot: RosterSlot
    members: tuple[RosterEntry, ...]


@dataclass(frozen=True, slots=True)
class RosterPlacement:
    """A complete immutable allocation of one roster ledger."""

    groups: tuple[RosterGroup, ...]
    waitlist: tuple[RosterEntry, ...]
    rejected: tuple[RosterEntry, ...]
    overflow: RosterOverflow

    @property
    def rejects_overflow(self) -> bool:
        """Whether full-slot entries were rejected rather than waitlisted."""
        return self.overflow is RosterOverflow.REJECT

    def group(self, slot: str) -> RosterGroup:
        """Return the allocation group named by ``slot``."""
        if group := next((group for group in self.groups if group.slot.key == slot), None):
            return group
        message = f"unknown roster slot {slot!r}"
        raise KeyError(message)

    def status(self, actor_id: str) -> RosterStatus | None:
        """Return the actual allocation outcome for ``actor_id``, if present."""
        if any(entry.actor_id == actor_id for group in self.groups for entry in group.members):
            return RosterStatus.SEATED
        if any(entry.actor_id == actor_id for entry in self.waitlist):
            return RosterStatus.WAITLISTED
        if any(entry.actor_id == actor_id for entry in self.rejected):
            return RosterStatus.REJECTED
        return None


def place_roster(
    entries: Sequence[RosterEntry],
    slots: Sequence[RosterSlot],
    *,
    overflow: RosterOverflow = RosterOverflow.WAITLIST,
) -> RosterPlacement:
    """Allocate a valid single-slot ledger in stable FIFO order."""
    slot_by_key = {slot.key: slot for slot in slots}
    if len(slot_by_key) != len(slots):
        message = "roster slot keys must be unique"
        raise ValueError(message)
    actor_ids = {entry.actor_id for entry in entries}
    if len(actor_ids) != len(entries):
        message = "a roster ledger may contain each actor only once"
        raise ValueError(message)
    if unknown := next((entry.slot for entry in entries if entry.slot not in slot_by_key), None):
        message = f"roster entry refers to unknown slot {unknown!r}"
        raise ValueError(message)

    dated = [(index, entry) for index, entry in enumerate(entries) if entry.joined_at is not None]
    undated = [(index, entry) for index, entry in enumerate(entries) if entry.joined_at is None]
    try:
        dated.sort(key=lambda item: (item[1].joined_at, item[0]))
    except TypeError as error:
        message = "roster joined_at values must use compatible timezone awareness"
        raise ValueError(message) from error

    members: dict[str, list[RosterEntry]] = {slot.key: [] for slot in slots}
    waitlist: list[RosterEntry] = []
    rejected: list[RosterEntry] = []
    for _index, entry in (*dated, *undated):
        slot = slot_by_key[entry.slot]
        if slot.capacity is None or len(members[slot.key]) < slot.capacity:
            members[slot.key].append(entry)
        elif overflow is RosterOverflow.WAITLIST:
            waitlist.append(entry)
        else:
            rejected.append(entry)

    return RosterPlacement(
        groups=tuple(RosterGroup(slot, tuple(members[slot.key])) for slot in slots),
        waitlist=tuple(waitlist),
        rejected=tuple(rejected),
        overflow=overflow,
    )


__all__ = [
    "RosterEntry",
    "RosterGroup",
    "RosterOverflow",
    "RosterPlacement",
    "RosterSlot",
    "RosterStatus",
    "place_roster",
]
