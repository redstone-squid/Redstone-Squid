"""Suggestion providers over the unified tag taxonomy.

These serve the same approved definitions the submission form's option sources publish, so a
restriction picked in Discord, on the web form, and in-game is spelled identically.
"""

from collections.abc import Sequence
from typing import Protocol

from squid.suggestions.application import Candidate, candidate
from squid.suggestions.domain import SuggestionRequest
from squid.suggestions.infrastructure.cache import TtlCache
from squid.suggestions.infrastructure.repository import TaxonomyEntry
from squid.tags.domain import TagDefinition


class TaxonomyReader(Protocol):
    """Read approved taxonomy values shaped for matching."""

    async def taxonomy(
        self,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
    ) -> Sequence[TaxonomyEntry]: ...


class PendingTagDefinitions(Protocol):
    """Read the tag definitions awaiting moderation."""

    async def pending(self) -> Sequence[TagDefinition]: ...


class TaxonomyProvider:
    """Suggest approved taxonomy values of one semantic kind."""

    def __init__(
        self,
        reader: TaxonomyReader,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
    ) -> None:
        self._reader = reader
        self._semantic_kind = semantic_kind
        self._build_kind = build_kind
        self._authority = authority
        self._cache = TtlCache[None, tuple[Candidate, ...]](self._load)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return await self._cache.get(None)

    async def _load(self, _key: None) -> tuple[Candidate, ...]:
        entries = _ordered(
            await self._reader.taxonomy(self._semantic_kind, build_kind=self._build_kind, authority=self._authority)
        )
        return tuple(
            # The stable key is submitted while the display name is what people read and type, so
            # both are matchable — as are aliases, which is the whole point of restriction aliases.
            candidate(
                value=entry.stable_key,
                label=entry.display_name,
                description=_alias_hint(entry),
                kind=entry.semantic_kind,
                terms=(entry.display_name, entry.stable_key, *entry.aliases),
            )
            for entry in entries
        )


class TaxonomyIdProvider:
    """Suggest taxonomy values by name while submitting the numeric tag id.

    Some commands persist restriction identifiers rather than names. Completing by name and
    submitting the id keeps those commands working unchanged while removing the step where a user
    had to look the number up somewhere else first.
    """

    def __init__(
        self,
        reader: TaxonomyReader,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
    ) -> None:
        self._reader = reader
        self._semantic_kind = semantic_kind
        self._build_kind = build_kind
        self._authority = authority
        self._cache = TtlCache[None, tuple[Candidate, ...]](self._load)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return await self._cache.get(None)

    async def _load(self, _key: None) -> tuple[Candidate, ...]:
        entries = _ordered(
            await self._reader.taxonomy(self._semantic_kind, build_kind=self._build_kind, authority=self._authority)
        )
        return tuple(
            candidate(
                value=str(entry.id),
                label=entry.display_name,
                description=_alias_hint(entry),
                kind=entry.semantic_kind,
                terms=(entry.display_name, entry.stable_key, str(entry.id), *entry.aliases),
            )
            for entry in entries
        )


class PendingTagProvider:
    """Suggest tag proposals awaiting a moderation decision, keyed by their numeric id."""

    def __init__(self, tags: PendingTagDefinitions) -> None:
        self._tags = tags

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        # Deliberately uncached: a moderator works down this list and must not be offered a
        # proposal they approved a second ago.
        return tuple(
            candidate(
                value=str(item.id),
                label=item.display_name,
                description=f"#{item.id} · {item.semantic_kind.value}",
                kind="tag",
                terms=(item.display_name, item.stable_key, str(item.id)),
            )
            for item in await self._tags.pending()
        )


def _ordered(entries: Sequence[TaxonomyEntry]) -> list[TaxonomyEntry]:
    """Sort in Python rather than trusting the database collation.

    The order decides the content revision an enumerable source publishes, and a revision that
    moves because a server's collation differs would look to clients like the option set changed.
    This is the same key `ApprovedSubmissionOptionCatalog` uses, so both catalogues agree.
    """
    return sorted(entries, key=lambda entry: (entry.display_name.casefold(), entry.stable_key))


def _alias_hint(entry: TaxonomyEntry) -> str | None:
    """Show why an alias matched, so a hit on a name that is not the label is not confusing."""
    if not entry.aliases:
        return None
    return "also " + ", ".join(entry.aliases[:3])
