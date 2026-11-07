"""Build the suggestion registry shared by the bot, API, and Minecraft adapters.

Adapters refer to sources by ID. Sources also used as submission form options
retain the same IDs in the form manifest.
"""

from typing import Protocol

from squid.core.i18n import SUPPORTED_LOCALES
from squid.permissions.domain.catalogue import (
    ACCOUNT_CLAIM_LIST,
    BUILD_SUBMISSION_VIEW_PENDING,
    TAG_PROPOSAL_LIST,
)
from squid.records.domain import BaseTitle, BuildKind, VersionScope
from squid.search.application.fields import FieldRegistry
from squid.suggestions.application import SuggestionRegistry, SuggestionSource
from squid.suggestions.application.queries import SuggestionRepository
from squid.suggestions.domain import SourceKind, ValueType, Visibility
from squid.suggestions.infrastructure.providers import (
    DocumentProvider,
    PendingTagProvider,
    SearchFieldProvider,
    SearchQueryProvider,
    SearchSortProvider,
    StaticProvider,
    TaxonomyProvider,
    VersionIdProvider,
    VersionProvider,
)
from squid.suggestions.infrastructure.providers.guilds import (
    GuildStarboards,
    PermissionRoleProvider,
    PermissionRoles,
    StarboardNameProvider,
    StarboardSettingProvider,
)
from squid.suggestions.infrastructure.providers.notifications import (
    AccountSubscriptions,
    SubscriptionProvider,
)
from squid.suggestions.infrastructure.providers.permissions import (
    PermissionNodeProvider,
    PermissionPatternProvider,
)
from squid.suggestions.infrastructure.providers.records import (
    AliasClaimProvider,
    CreatorProfileProvider,
    CreatorProvider,
    PendingAliasClaims,
    RecordDefinitionProvider,
    RecordTitleProvider,
)
from squid.suggestions.infrastructure.providers.taxonomy import PendingTagDefinitions
from squid.suggestions.infrastructure.providers.versions import CanonicalMinecraftVersions
from squid.tags.domain import TagSemanticKind

PUBLIC_BUILD_STATUSES = ("confirmed",)
"""Build statuses exposed by public suggestion sources."""

PENDING_BUILD_STATUSES = ("pending",)


class SearchFields(Protocol):
    """Read the effective public field registry."""

    async def fields(self) -> FieldRegistry: ...


def build_registry(
    *,
    repository: SuggestionRepository,
    search: SearchFields,
    versions: CanonicalMinecraftVersions,
    tags: PendingTagDefinitions,
    starboards: GuildStarboards | None = None,
    permission_roles: PermissionRoles | None = None,
    notifications: AccountSubscriptions | None = None,
    accounts: PendingAliasClaims | None = None,
) -> SuggestionRegistry:
    """Assemble every source this deployment can answer.

    Optional services add guild starboards, permission roles, subscriptions,
    and pending claims. Their sources are absent when the service is not supplied.
    All providers use the supplied repository or services for their reads.
    """
    return SuggestionRegistry.of(
        (
            *_taxonomy_sources(repository),
            *_search_sources(search, repository),
            *_document_sources(repository),
            *_static_sources(),
            *_permission_sources(),
            *_guild_sources(starboards, permission_roles),
            *_viewer_sources(notifications),
            *_claim_sources(accounts),
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
            SuggestionSource(
                id="version_ids",
                provider=VersionIdProvider(repository),
                kind=SourceKind.ENUMERABLE,
                value_type=ValueType.INTEGER,
                kind_label="version",
            ),
            SuggestionSource(
                id="title_definitions",
                provider=RecordDefinitionProvider(repository),
                kind_label="record_category",
            ),
            SuggestionSource(
                id="creators",
                provider=CreatorProvider(repository),
                kind_label="creator",
                multi_value=",",
            ),
            SuggestionSource(
                id="creator_profiles",
                provider=CreatorProfileProvider(repository),
                kind_label="creator",
            ),
            SuggestionSource(
                id="record_titles",
                provider=RecordTitleProvider(repository),
                kind_label="record_title",
            ),
        )
    )


def _guild_sources(
    starboards: GuildStarboards | None,
    permission_roles: PermissionRoles | None,
) -> tuple[SuggestionSource, ...]:
    sources: list[SuggestionSource] = [
        SuggestionSource(
            id="starboard_settings",
            provider=StarboardSettingProvider(),
            kind=SourceKind.ENUMERABLE,
            kind_label="starboard_setting",
        )
    ]
    if starboards is not None:
        sources.append(
            SuggestionSource(
                id="starboard_names",
                provider=StarboardNameProvider(starboards),
                context_keys=frozenset({"guild_id"}),
                kind_label="starboard",
            )
        )
    if permission_roles is not None:
        sources.append(
            SuggestionSource(
                id="permission_roles",
                provider=PermissionRoleProvider(permission_roles),
                context_keys=frozenset({"guild_id"}),
                kind_label="permission_role",
            )
        )
    return tuple(sources)


def _claim_sources(accounts: PendingAliasClaims | None) -> tuple[SuggestionSource, ...]:
    if accounts is None:
        return ()
    return (
        SuggestionSource(
            id="alias_claims_pending",
            provider=AliasClaimProvider(accounts),
            visibility=Visibility.REQUIRES_NODE,
            required_node=ACCOUNT_CLAIM_LIST.name,
            value_type=ValueType.INTEGER,
            kind_label="claim",
        ),
    )


def _viewer_sources(notifications: AccountSubscriptions | None) -> tuple[SuggestionSource, ...]:
    if notifications is None:
        return ()
    return (
        SuggestionSource(
            id="notification_subscriptions",
            provider=SubscriptionProvider(notifications),
            visibility=Visibility.VIEWER_SCOPED,
            value_type=ValueType.INTEGER,
            kind_label="subscription",
        ),
    )


def _taxonomy_sources(repository: SuggestionRepository) -> tuple[SuggestionSource, ...]:
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


def _search_sources(
    search: SearchFields,
    repository: SuggestionRepository,
) -> tuple[SuggestionSource, ...]:
    return (
        SuggestionSource(
            id="search_query",
            provider=SearchQueryProvider(search, repository),
            kind_label="query",
        ),
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


def _document_sources(repository: SuggestionRepository) -> tuple[SuggestionSource, ...]:
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


def _static_sources() -> tuple[SuggestionSource, ...]:
    return (
        SuggestionSource(
            id="build_kinds",
            provider=StaticProvider.of([kind.value for kind in BuildKind], kind="build_kind"),
            kind=SourceKind.ENUMERABLE,
            kind_label="build_kind",
        ),
        SuggestionSource(
            id="base_titles",
            provider=StaticProvider.of([item.value for item in BaseTitle], kind="base_title"),
            kind=SourceKind.ENUMERABLE,
            kind_label="base_title",
        ),
        SuggestionSource(
            id="version_scopes",
            provider=StaticProvider.of([item.value for item in VersionScope], kind="version_scope"),
            kind=SourceKind.ENUMERABLE,
            kind_label="version_scope",
        ),
        SuggestionSource(
            id="locales",
            provider=StaticProvider.of(sorted(SUPPORTED_LOCALES), kind="locale"),
            kind=SourceKind.ENUMERABLE,
            kind_label="locale",
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
