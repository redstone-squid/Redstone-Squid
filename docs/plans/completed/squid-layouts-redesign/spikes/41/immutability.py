"""Hashability checks and equality handling shared by the reactivity prototypes.

Hashing catches lists nested inside tuples or generated frozen-dataclass hashes. It does
not prove immutability: mutable objects with identity-based or custom hashes can pass.
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
    """Treat identical objects as equal and failed equality comparisons as unequal."""
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:
        return False
