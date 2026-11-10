"""Suggestion providers over the search projection.

Builds and computed records are far too numerous to enumerate, so these push matching into
Postgres. They return a finished result rather than candidates: the database has already ranked by
trigram similarity against the indexed fuzzy text, and re-ranking that with the in-memory matcher
would only discard rows it cannot see a literal reason to keep.
"""

from collections.abc import Collection, Sequence
from typing import Protocol

from squid.suggestions.application.queries import DocumentEntry
from squid.suggestions.domain import MAX_SUGGESTIONS, Suggestion, SuggestionRequest, SuggestionResult


class DocumentReader(Protocol):
    """Match projected resources by identifier or title."""

    async def documents(
        self,
        resource_kind: str,
        query: str,
        *,
        statuses: Collection[str] | None = None,
        limit: int,
    ) -> Sequence[DocumentEntry]: ...


class DocumentProvider:
    """Suggest projected resources of one kind, optionally restricted to some statuses."""

    def __init__(
        self,
        reader: DocumentReader,
        resource_kind: str,
        *,
        statuses: Collection[str] | None = None,
        kind_label: str = "",
        by_title: bool = False,
    ) -> None:
        self._reader = reader
        self._resource_kind = resource_kind
        self._statuses = statuses
        self._kind_label = kind_label or resource_kind
        self._by_title = by_title

    async def suggest(self, request: SuggestionRequest) -> SuggestionResult:
        entries = await self._reader.documents(
            self._resource_kind,
            request.query,
            statuses=self._statuses,
            limit=request.limit or MAX_SUGGESTIONS,
        )
        return SuggestionResult(
            items=tuple(
                Suggestion(
                    # A free-text search box wants words to search for; a command parameter that
                    # names one build wants its identifier. Same rows, different answer.
                    value=entry.title if self._by_title else entry.source_key,
                    # Lead with the identifier users read from an embed and paste back.
                    label=entry.title if self._by_title else f"#{entry.source_key} — {entry.title}",
                    description=entry.subtitle,
                    kind=self._kind_label,
                )
                for entry in entries
            )
        )
