"""Typed keys for ephemeral values provided through a component tree."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, eq=False)
class ContextKey[ValueT]:
    """Typed identity for an ephemeral runtime context value."""

    name: str
