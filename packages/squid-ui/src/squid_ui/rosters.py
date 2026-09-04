"""Immutable roster values shared by semantic rendering and downstream pattern packages.

Named in the plural for the same reason as `grids`: `factories.roster` is the factory.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from squid_ui.palette import Tone
from squid_ui.text import TextLike


@dataclass(frozen=True, slots=True)
class RosterSlot:
    """One destination in a roster; raises `ValueError` for an empty `key` or a negative `capacity`."""

    key: str
    label: TextLike
    capacity: int | None = None
    """Seats before `RosterOverflow` applies; `None` never fills."""
    tone: Tone = Tone.NEUTRAL

    def __post_init__(self) -> None:
        if not self.key:
            message = "roster slot key must not be empty"
            raise ValueError(message)
        if self.capacity is not None and self.capacity < 0:
            message = "roster slot capacity must not be negative"
            raise ValueError(message)


class RosterOverflow(StrEnum):
    """What `place_roster` does with an entry whose requested slot is full."""

    REJECT = "reject"
    """The entry lands in `RosterPlacement.rejected` and holds no place."""
    WAITLIST = "waitlist"
    """The entry lands in `RosterPlacement.waitlist`, in arrival order across every slot."""


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """One actor's requested roster slot; raises `ValueError` for an empty `actor_id` or `slot`."""

    actor_id: str
    display: TextLike
    slot: str
    """Key of the requested `RosterSlot`."""
    joined_at: datetime | None = None
    """Orders allocation: dated entries first by time, then undated ones in ledger order."""

    def __post_init__(self) -> None:
        if not self.actor_id:
            message = "roster actor id must not be empty"
            raise ValueError(message)
        if not self.slot:
            message = "roster entry slot must not be empty"
            raise ValueError(message)


class RosterStatus(StrEnum):
    """Where `place_roster` put one actor: a slot's `members`, the `waitlist`, or `rejected`."""

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
        """The group for the slot keyed `slot`; raises `KeyError` when the placement has no such slot."""
        if group := next((group for group in self.groups if group.slot.key == slot), None):
            return group
        message = f"unknown roster slot {slot!r}"
        raise KeyError(message)

    def status(self, actor_id: str) -> RosterStatus | None:
        """Where `actor_id` was placed, or `None` when the ledger had no entry for it."""
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
    """Seat each entry in its requested slot in arrival order; the rest overflow per `overflow`.

    Arrival order is `joined_at` ascending with ledger order as the tiebreak, undated entries after
    every dated one. Raises `ValueError` for duplicate slot keys, an actor listed twice, an entry
    naming an unknown slot, or `joined_at` values that mix naive and aware datetimes.
    """
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
