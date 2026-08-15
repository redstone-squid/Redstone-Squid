"""Suggestion providers over the search projection.

Builds and computed records are far too numerous to enumerate, so these push matching into
Postgres. They return a finished result rather than candidates: the database has already ranked by
trigram similarity against the indexed fuzzy text, and re-ranking that with the in-memory matcher
would only discard rows it cannot see a literal reason to keep.
"""

from collections.abc import Collection, Sequence
from typing import Protocol

from squid.suggestions.domain import MAX_SUGGESTIONS, Suggestion, SuggestionRequest, SuggestionResult
from squid.suggestions.infrastructure.repository import DocumentEntry


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
    ) -> None:
        self._reader = reader
        self._resource_kind = resource_kind
        self._statuses = statuses
        self._kind_label = kind_label or resource_kind

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
                    value=entry.source_key,
                    label=_label(entry),
                    description=entry.subtitle,
                    kind=self._kind_label,
                )
                for entry in entries
            )
        )


def _label(entry: DocumentEntry) -> str:
    """Lead with the identifier, because that is what a user reads off an embed and pastes back."""
    return f"#{entry.source_key} — {entry.title}"
