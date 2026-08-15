"""Adapters producing suggestion candidates from application services and persistence."""

from squid.suggestions.infrastructure.providers.documents import DocumentProvider
from squid.suggestions.infrastructure.providers.search_fields import SearchFieldProvider, SearchSortProvider
from squid.suggestions.infrastructure.providers.search_query import SearchQueryProvider
from squid.suggestions.infrastructure.providers.static import CallableProvider, StaticProvider
from squid.suggestions.infrastructure.providers.taxonomy import (
    PendingTagProvider,
    TaxonomyIdProvider,
    TaxonomyProvider,
)
from squid.suggestions.infrastructure.providers.versions import VersionIdProvider, VersionProvider

__all__ = [
    "CallableProvider",
    "DocumentProvider",
    "PendingTagProvider",
    "SearchFieldProvider",
    "SearchQueryProvider",
    "SearchSortProvider",
    "StaticProvider",
    "TaxonomyIdProvider",
    "TaxonomyProvider",
    "VersionIdProvider",
    "VersionProvider",
]
