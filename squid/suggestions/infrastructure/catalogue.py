"""The registered suggestion sources.

This module is the public vocabulary: adding a source here makes it completable from Discord, from
`GET /v1/suggest/{source}`, and from a Minecraft command at once. Ids that already exist as
submission form `option_source` values keep their spelling so the form manifest and the suggestion
registry never disagree about what a name means.
"""

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.permissions.domain.catalogue import (
    BUILD_SUBMISSION_VIEW_PENDING,
    TAG_PROPOSAL_LIST,
)
from squid.records.domain import BuildKind, RecordClass, VersionScope
from squid.search.application.fields import FieldRegistry
from squid.suggestions.application import SuggestionRegistry, SuggestionSource
from squid.suggestions.domain import SourceKind, ValueType, Visibility
from squid.suggestions.infrastructure.providers import (
    DocumentProvider,
    PendingTagProvider,
    SearchFieldProvider,
    SearchSortProvider,
    StaticProvider,
    TaxonomyProvider,
    VersionProvider,
)
from squid.suggestions.infrastructure.providers.permissions import (
    PermissionNodeProvider,
    PermissionPatternProvider,
)
from squid.suggestions.infrastructure.repository import PostgresSuggestionRepository
from squid.tags.domain import TagDefinition, TagSemanticKind
from squid.versions.domain import MinecraftVersion

PUBLIC_BUILD_STATUSES = ("confirmed",)
"""Build statuses anyone may be offered, matching the public search fence."""

PENDING_BUILD_STATUSES = ("pending",)


class SearchFields(Protocol):
    """Read the effective public field registry."""

    async def fields(self) -> FieldRegistry: ...


class CanonicalMinecraftVersions(Protocol):
    """Read canonical versions recognized by build persistence."""

    async def list_all(self) -> Sequence[MinecraftVersion]: ...


class PendingTagDefinitions(Protocol):
    """Read the tag definitions awaiting moderation."""

    async def pending(self) -> Sequence[TagDefinition]: ...


def build_registry(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    search: SearchFields,
    versions: CanonicalMinecraftVersions,
    tags: PendingTagDefinitions,
) -> SuggestionRegistry:
    """Assemble every source this deployment can answer."""
    repository = PostgresSuggestionRepository(session_factory)
    return SuggestionRegistry.of(
        (
            *_taxonomy_sources(repository),
            *_search_sources(search),
            *_document_sources(repository),
            *_static_sources(),
            *_permission_sources(),
            SuggestionSource(
                id="approved_source_versions",
                provider=VersionProvider(versions),
                kind=SourceKind.ENUMERABLE,
                kind_label="version",
            ),
            SuggestionSource(
                id="tags_pending",
                provider=PendingTagProvider(tags),
                visibility=Visibility.REQUIRES_NODE,
                required_node=TAG_PROPOSAL_LIST.name,
                value_type=ValueType.INTEGER,
                kind_label="tag",
            ),
        )
    )


def _taxonomy_sources(repository: PostgresSuggestionRepository) -> tuple[SuggestionSource, ...]:
    return (
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
            id="approved_showcase_tags",
            provider=TaxonomyProvider(repository, TagSemanticKind.SHOWCASE.value),
            kind=SourceKind.ENUMERABLE,
            multi_value=",",
            kind_label="showcase",
        ),
        SuggestionSource(
            id="door_types",
            # Door types are patterns declared applicable to doors, not a separate taxonomy.
            provider=TaxonomyProvider(repository, TagSemanticKind.PATTERN.value, build_kind="Door"),
            kind=SourceKind.ENUMERABLE,
            kind_label="pattern",
        ),
    )


def _search_sources(search: SearchFields) -> tuple[SuggestionSource, ...]:
    return (
        SuggestionSource(
            id="search_fields",
            provider=SearchFieldProvider(search),
            kind=SourceKind.ENUMERABLE,
            kind_label="field",
        ),
        SuggestionSource(
            id="search_sorts",
            provider=SearchSortProvider(search),
            kind=SourceKind.ENUMERABLE,
            kind_label="sort",
        ),
    )


def _document_sources(repository: PostgresSuggestionRepository) -> tuple[SuggestionSource, ...]:
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
            id="records",
            provider=DocumentProvider(repository, "record", kind_label="record"),
            kind_label="record",
        ),
    )


def _static_sources() -> tuple[SuggestionSource, ...]:
    return (
        SuggestionSource(
            id="build_kinds",
            provider=StaticProvider.of([kind.value for kind in BuildKind], kind="build_kind"),
            kind=SourceKind.ENUMERABLE,
            kind_label="build_kind",
        ),
        SuggestionSource(
            id="record_classes",
            provider=StaticProvider.of([item.value for item in RecordClass], kind="record_class"),
            kind=SourceKind.ENUMERABLE,
            kind_label="record_class",
        ),
        SuggestionSource(
            id="version_scopes",
            provider=StaticProvider.of([item.value for item in VersionScope], kind="version_scope"),
            kind=SourceKind.ENUMERABLE,
            kind_label="version_scope",
        ),
    )


def _permission_sources() -> tuple[SuggestionSource, ...]:
    return (
        SuggestionSource(
            id="permission_nodes",
            provider=PermissionNodeProvider(),
            kind=SourceKind.ENUMERABLE,
            kind_label="permission_node",
        ),
        SuggestionSource(
            id="permission_patterns",
            provider=PermissionPatternProvider(),
            kind=SourceKind.ENUMERABLE,
            kind_label="permission_pattern",
        ),
    )
