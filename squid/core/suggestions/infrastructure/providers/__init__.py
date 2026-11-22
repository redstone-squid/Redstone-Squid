"""Adapters producing suggestion candidates from application services and persistence."""

from squid.core.suggestions.infrastructure.providers.documents import DocumentProvider
from squid.core.suggestions.infrastructure.providers.search_fields import SearchFieldProvider, SearchSortProvider
from squid.core.suggestions.infrastructure.providers.search_query import SearchQueryProvider
from squid.core.suggestions.infrastructure.providers.static import CallableProvider, StaticProvider
from squid.core.suggestions.infrastructure.providers.tags import (
    PendingTagProvider,
    TagProvider,
)
from squid.core.suggestions.infrastructure.providers.versions import VersionIdProvider, VersionProvider

__all__ = [
    "CallableProvider",
    "DocumentProvider",
    "PendingTagProvider",
    "SearchFieldProvider",
    "SearchQueryProvider",
    "SearchSortProvider",
    "StaticProvider",
    "TagProvider",
    "VersionIdProvider",
    "VersionProvider",
]
