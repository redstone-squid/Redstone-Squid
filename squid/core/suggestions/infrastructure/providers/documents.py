"""Suggestion providers over the search projection.

Builds and computed records are far too numerous to enumerate, so these push matching into
Postgres. They return a finished result rather than candidates: the database has already ranked by
trigram similarity against the indexed fuzzy text, and re-ranking that with the in-memory matcher
would only discard rows it cannot see a literal reason to keep.
"""

from collections.abc import Collection, Sequence
from typing import Protocol

from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_VIEW_PENDING
from squid.core.suggestions.application.queries import DocumentEntry, SuggestionRepository
from squid.core.suggestions.application.registry import SuggestionSource
from squid.core.suggestions.domain import (
    MAX_QUERY_SUGGESTIONS,
    Suggestion,
    SuggestionRequest,
    SuggestionResult,
    ValueType,
    Visibility,
)


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
            limit=request.limit or MAX_QUERY_SUGGESTIONS,
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


PUBLIC_BUILD_STATUSES = ("confirmed",)
PENDING_BUILD_STATUSES = ("pending",)


def document_sources(repository: SuggestionRepository) -> tuple[SuggestionSource, ...]:
    return (
        SuggestionSource(
            id="builds",
            provider=DocumentProvider(repository, "build", statuses=PUBLIC_BUILD_STATUSES, kind_label="build"),
            value_type=ValueType.INTEGER,
            kind_label="build",
        ),
        SuggestionSource(
            id="builds_pending",
            # Gated because unreviewed submissions are not public, and Discord does not run a
            # command's permission checks before its autocomplete callback.
            provider=DocumentProvider(repository, "build", statuses=PENDING_BUILD_STATUSES, kind_label="build"),
            visibility=Visibility.REQUIRES_NODE,
            required_node=BUILD_SUBMISSION_VIEW_PENDING.name,
            value_type=ValueType.INTEGER,
            kind_label="build",
        ),
        SuggestionSource(
            # Titles rather than ids: this backs a free-text search box, where the answer is words
            # to search for.
            id="build_titles",
            provider=DocumentProvider(
                repository, "build", statuses=PUBLIC_BUILD_STATUSES, kind_label="build", by_title=True
            ),
            kind_label="build",
        ),
        SuggestionSource(
            id="records",
            provider=DocumentProvider(repository, "record", kind_label="record"),
            kind_label="record",
        ),
    )
