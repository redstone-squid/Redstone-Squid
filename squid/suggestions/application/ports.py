"""Extension points implemented by suggestion adapters."""

from typing import Protocol, runtime_checkable

from squid.suggestions.application.matching import Candidate
from squid.suggestions.domain import SuggestionRequest, SuggestionResult


@runtime_checkable
class SuggestionProvider(Protocol):
    """Produce candidates for one source.

    A provider returns candidates rather than a finished result so the service applies one ranking
    and one limit everywhere.
    """

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        """Return candidates in a sensible default order, which the matcher keeps for an empty query.

        May exceed `request.limit`; the service ranks and truncates. `request.limit == 0` asks for
        the full set.
        """
        ...


@runtime_checkable
class ComposedSuggestionProvider(Protocol):
    """Produce a finished result for a source that ranks or splices for itself.

    Query-language completion is the motivating case: the token the caret sits in changes both the
    candidate set and the span being replaced, which a flat candidate list cannot carry.
    """

    async def suggest(self, request: SuggestionRequest) -> SuggestionResult:
        """Return ranked items already truncated to `request.limit`; the service does not re-rank them."""
        ...


class SuggestionAuthorizer(Protocol):
    """Decide whether the caller behind a request holds a permission node.

    Passed per call rather than held by the service because each transport resolves a subject
    differently: the bot from an interaction, the API from an authenticated caller.
    """

    async def allows(self, node: str) -> bool:
        """Return whether the caller holds `node`.

        A raise is treated as a refusal: `SuggestionService.suggest` logs it and answers with an
        empty result rather than propagating it into a half-typed word.
        """
        ...
