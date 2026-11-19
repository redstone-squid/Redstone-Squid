"""Compose provider-owned suggestion sources for one deployment."""

from collections.abc import Iterable

from squid.core.suggestions.application import SuggestionRegistry, SuggestionSource
from squid.core.suggestions.application.ports import SearchFields
from squid.core.suggestions.application.queries import SuggestionRepository
from squid.core.suggestions.infrastructure.providers.documents import document_sources
from squid.core.suggestions.infrastructure.providers.guilds import GuildStarboards, PermissionRoles, guild_sources
from squid.core.suggestions.infrastructure.providers.notifications import AccountSubscriptions, notification_sources
from squid.core.suggestions.infrastructure.providers.permissions import permission_sources
from squid.core.suggestions.infrastructure.providers.records import PendingAliasClaims, record_sources
from squid.core.suggestions.infrastructure.providers.search_query import search_sources
from squid.core.suggestions.infrastructure.providers.static import static_sources
from squid.core.suggestions.infrastructure.providers.taxonomy import PendingTagDefinitions, taxonomy_sources
from squid.core.suggestions.infrastructure.providers.versions import CanonicalMinecraftVersions, version_sources


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
    extra_sources: Iterable[SuggestionSource] = (),
) -> SuggestionRegistry:
    """Assemble every source this deployment can answer.

    Optional services add guild starboards, permission roles, subscriptions,
    and pending claims. Their sources are absent when the service is not supplied.
    All providers use the supplied repository or services for their reads.
    """
    return SuggestionRegistry.of(
        (
            *taxonomy_sources(repository, tags),
            *search_sources(search, repository),
            *document_sources(repository),
            *static_sources(),
            *permission_sources(),
            *guild_sources(starboards, permission_roles),
            *notification_sources(notifications),
            *record_sources(repository, accounts),
            *version_sources(versions, repository),
            *extra_sources,
        )
    )
