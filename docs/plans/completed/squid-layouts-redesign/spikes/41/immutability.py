"""Shared by both prototypes: what counts as a state value once mutation is out.

Hashability is the check. It is not a perfect immutability test -- a plain mutable
object hashes by identity -- but it is *deep*, which the annotation check plan 40
used to carry was not: `(1, [2])` and a frozen dataclass with a `list` field both
fail, and those are the cases that actually bite.
"""


class MutableStateError(TypeError):
    """A state field was assigned a value that cannot be treated as a snapshot."""


def check(label: str, value: object) -> None:
    try:
        hash(value)
    except TypeError as error:
        message = (
            f"{label} was assigned {type(value).__name__}, which is mutable. State is "
            f"replaced, not mutated -- use tuple/frozenset/a frozen dataclass. ({error})"
        )
        raise MutableStateError(message) from error


def equal(left: object, right: object) -> bool:
    """The conservative comparison `_Computed.refresh_for` already uses."""
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:
        return False
