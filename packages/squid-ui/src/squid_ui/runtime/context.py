"""Typed keys for ephemeral values provided through a component tree."""

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, eq=False)
class ContextKey[ValueT]:
    """Typed identity for a value passed down a component tree through `provide`/`inject`.

    Keys compare by identity, so two keys with the same `name` are different keys; `name` only
    appears in the `LookupError` `inject` raises.
    """

    name: str
    cache_version: Callable[[ValueT], object] | None = field(default=None, repr=False)
    """Projects a value to what a cached render may depend on: two values with equal projections
    share a cached render. It must change whenever callbacks, authority, or any render-observable
    behaviour changes. `None` matches by identity only."""

    def matches(self, left: ValueT, right: ValueT) -> bool:
        """Whether two provided values may share a cached component render.

        Identical objects always match. A `cache_version` that raises counts as no match.
        """
        if left is right:
            return True
        if self.cache_version is None:
            return False
        try:
            version = self.cache_version
            return version(left) == version(right)
        except Exception:
            return False
