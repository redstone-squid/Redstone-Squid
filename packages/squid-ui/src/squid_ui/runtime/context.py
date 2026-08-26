"""Typed keys for ephemeral values provided through a component tree."""

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, eq=False)
class ContextKey[ValueT]:
    """Typed identity and optional cache-version contract for an ephemeral context value.

    Args:
        name: Diagnostic name for the provided value.
        cache_version: Projection that certifies when distinct values are interchangeable for
            component render caching. It must change whenever callbacks, authority, or any
            render-observable behavior changes. Without it, cache matching uses identity.
    """

    name: str
    cache_version: Callable[[ValueT], object] | None = field(default=None, repr=False)

    def matches(self, left: object, right: object) -> bool:
        """Whether two provided values may share a cached component render."""
        if left is right:
            return True
        if self.cache_version is None:
            return False
        try:
            version = self.cache_version  # Preserve the generic narrowing for both calls.
            return version(left) == version(right)  # pyrefly: ignore[bad-argument-type]
        except Exception:
            return False
