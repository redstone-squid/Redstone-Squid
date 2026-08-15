"""Extension points implemented by suggestion adapters."""

from typing import Protocol, runtime_checkable

from squid.suggestions.application.matching import Candidate
from squid.suggestions.domain import SuggestionRequest, SuggestionResult


@runtime_checkable
class SuggestionProvider(Protocol):
    """Produce candidates for one source.

    A provider returns candidates rather than a finished result so the service applies one ranking
    and one limit everywhere. Providers that select candidates themselves — anything backed by a
    database query — should still return them in a sensible default order, because the shared
    matcher preserves that order for an empty query.
    """

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]: ...


@runtime_checkable
class ComposedSuggestionProvider(Protocol):
    """Produce a finished result for a source that ranks or splices for itself.

    Query-language completion is the motivating case: it decides which token the caret sits in,
    which changes both the candidate set and the span being replaced, so it cannot be expressed as
    a flat candidate list.
    """

    async def suggest(self, request: SuggestionRequest) -> SuggestionResult: ...


class SuggestionAuthorizer(Protocol):
    """Decide whether the caller behind a request holds a permission node.

    Passed per call rather than held by the service because each transport resolves a subject
    differently: the bot from an interaction, the API from an authenticated principal.
    """

    async def allows(self, node: str) -> bool: ...
