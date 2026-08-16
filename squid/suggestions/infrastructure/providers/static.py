"""Providers for candidate sets that are fixed in code.

These exist so a `Literal` that a slash command renders as native choices is still available to
the web form and to Minecraft, where no such rendering exists — and so the vocabulary is written
down once instead of being re-typed per surface.
"""

from collections.abc import Callable, Iterable, Sequence

from squid.suggestions.application import Candidate, candidate
from squid.suggestions.domain import SuggestionRequest


class StaticProvider:
    """Suggest from a fixed candidate list."""

    def __init__(self, values: Iterable[Candidate]) -> None:
        self._candidates = tuple(values)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return self._candidates

    @classmethod
    def of(cls, values: Sequence[str], *, kind: str = "") -> StaticProvider:
        """Build a provider whose value and label are the same string."""
        return cls(candidate(value, kind=kind) for value in values)

    @classmethod
    def labelled(cls, values: Sequence[tuple[str, str]], *, kind: str = "") -> StaticProvider:
        """Build a provider from `(value, label)` pairs."""
        return cls(candidate(value, label, kind=kind) for value, label in values)


class CallableProvider:
    """Suggest from candidates computed per request by a plain callable.

    The escape hatch for surface-local sources — Discord command names, the settings keys a cog
    parses — that have no persistence behind them and no reason to grow a class.
    """

    def __init__(self, produce: Callable[[SuggestionRequest], Iterable[Candidate]]) -> None:
        self._produce = produce

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        return tuple(self._produce(request))
