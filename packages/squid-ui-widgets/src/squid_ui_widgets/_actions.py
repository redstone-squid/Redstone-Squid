"""Structured action names shared by nested state-machine widgets."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Self


class MachineKeySegment(str):
    """A non-empty machine key that cannot consume another colon-delimited action field."""

    def __new__(cls, value: str, *, name: str = "MachineKeySegment") -> Self:
        if not isinstance(value, str):
            message = f"{name} must be a string"
            raise TypeError(message)
        if not value:
            message = f"{name} must not be empty"
            raise ValueError(message)
        if ":" in value:
            message = f"{name} must not contain ':'"
            raise ValueError(message)
        return str.__new__(cls, value)


class PageDirection(StrEnum):
    """A paging action's only two valid directions."""

    PREVIOUS = "previous"
    NEXT = "next"

    @property
    def delta(self) -> int:
        return -1 if self is PageDirection.PREVIOUS else 1


@dataclass(frozen=True, slots=True)
class PageAction:
    """One strictly parsed ``page:<key>:<direction>`` action."""

    key: MachineKeySegment
    direction: PageDirection

    def encode(self) -> str:
        return f"page:{self.key}:{self.direction}"

    @classmethod
    def parse(cls, action: str) -> Self | None:
        match action.split(":"):
            case ["page", raw_key, raw_direction]:
                try:
                    return cls(MachineKeySegment(raw_key), PageDirection(raw_direction))
                except ValueError:
                    return None
            case _:
                return None


def keyed_action(verb: str, key: MachineKeySegment) -> str:
    """Encode an action with one validated key field."""
    return f"{MachineKeySegment(verb, name='action verb')}:{key}"


def match_keyed_action(action: str, verb: str) -> MachineKeySegment | None:
    """Return one exact key field, rejecting trailing or embedded action fields."""
    expected = MachineKeySegment(verb, name="action verb")
    match action.split(":"):
        case [name, raw_key] if name == expected:
            try:
                return MachineKeySegment(raw_key)
            except ValueError:
                return None
        case _:
            return None


@dataclass(frozen=True, slots=True)
class NestedAction:
    """An action namespaced under one validated machine key."""

    key: MachineKeySegment
    action: str

    def __post_init__(self) -> None:
        if not self.action:
            message = "nested action must not be empty"
            raise ValueError(message)

    def encode(self) -> str:
        return f"section:{self.key}:{self.action}"

    @classmethod
    def parse(cls, action: str) -> Self | None:
        match action.split(":", 2):
            case ["section", raw_key, nested] if nested:
                try:
                    return cls(MachineKeySegment(raw_key), nested)
                except ValueError:
                    return None
            case _:
                return None
