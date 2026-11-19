"""Suggestion providers over the unified tag taxonomy.

These serve the same approved definitions the submission form's option sources publish, so a
restriction picked in Discord, on the web form, and in-game is spelled identically.
"""

from collections.abc import Sequence
from typing import Protocol

from squid.permissions.domain.catalogue import TAG_PROPOSAL_LIST
from squid.suggestions.application import Candidate
from squid.suggestions.application.queries import TaxonomyEntry
from squid.suggestions.application.registry import SuggestionSource
from squid.suggestions.domain import SourceKind, SuggestionRequest, ValueType, Visibility
from squid.suggestions.infrastructure.cache import TtlCache
from squid.tags.domain import TagSemanticKind
from squid.tags.models import TagDefinition


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
    """Suggest approved taxonomy names, submitting stable keys or numeric IDs."""

    def __init__(
        self,
        reader: TaxonomyReader,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
        by_id: bool = False,
    ) -> None:
        self._reader = reader
        self._semantic_kind = semantic_kind
        self._build_kind = build_kind
        self._authority = authority
        self._by_id = by_id
        self._cache = TtlCache[None, tuple[Candidate, ...]](self._load)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return await self._cache.get(None)

    async def _load(self, _key: None) -> tuple[Candidate, ...]:
        entries = _ordered(
            await self._reader.taxonomy(self._semantic_kind, build_kind=self._build_kind, authority=self._authority)
        )
        return tuple(
            Candidate.create(
                value=str(entry.id) if self._by_id else entry.stable_key,
                label=entry.display_name,
                description=_alias_hint(entry),
                kind=entry.semantic_kind,
                terms=(entry.display_name, entry.stable_key, *entry.aliases)
                + ((str(entry.id),) if self._by_id else ()),
                value_type=entry.value_type,
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
            Candidate.create(
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


def taxonomy_sources(repository: TaxonomyReader, tags: PendingTagDefinitions) -> tuple[SuggestionSource, ...]:
    return (
        SuggestionSource(
            id="tags_pending",
            provider=PendingTagProvider(tags),
            visibility=Visibility.REQUIRES_NODE,
            required_node=TAG_PROPOSAL_LIST.name,
            value_type=ValueType.INTEGER,
            kind_label="tag",
        ),
        SuggestionSource(
            id="approved_restrictions",
            provider=TaxonomyProvider(repository, TagSemanticKind.RESTRICTION.value),
            kind=SourceKind.ENUMERABLE,
            multi_value=",",
            kind_label="restriction",
        ),
        SuggestionSource(
            id="approved_patterns",
            provider=TaxonomyProvider(repository, TagSemanticKind.PATTERN.value),
            kind=SourceKind.ENUMERABLE,
            multi_value=",",
            kind_label="pattern",
        ),
        SuggestionSource(
            # Showcase tags are proposed by users, so unlike restrictions and patterns they are
            # never `official` and must not be filtered on authority.
            id="approved_showcase_tags",
            provider=TaxonomyProvider(repository, TagSemanticKind.SHOWCASE.value, authority=None),
            kind=SourceKind.ENUMERABLE,
            multi_value=",",
            kind_label="showcase",
        ),
        SuggestionSource(
            id="showcase_tag_ids",
            provider=TaxonomyProvider(repository, TagSemanticKind.SHOWCASE.value, authority=None, by_id=True),
            kind=SourceKind.ENUMERABLE,
            value_type=ValueType.INTEGER,
            kind_label="showcase",
        ),
        SuggestionSource(
            id="restriction_ids",
            provider=TaxonomyProvider(repository, TagSemanticKind.RESTRICTION.value, by_id=True),
            kind=SourceKind.ENUMERABLE,
            value_type=ValueType.INTEGER,
            multi_value=",",
            kind_label="restriction",
        ),
        SuggestionSource(
            id="door_types",
            # Door types are patterns declared applicable to doors, not a separate taxonomy.
            provider=TaxonomyProvider(repository, TagSemanticKind.PATTERN.value, build_kind="Door"),
            kind=SourceKind.ENUMERABLE,
            kind_label="pattern",
        ),
    )
