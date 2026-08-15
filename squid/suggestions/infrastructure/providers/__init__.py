"""Adapters producing suggestion candidates from application services and persistence."""

from squid.suggestions.infrastructure.providers.documents import DocumentProvider
from squid.suggestions.infrastructure.providers.search_fields import SearchFieldProvider, SearchSortProvider
from squid.suggestions.infrastructure.providers.static import CallableProvider, StaticProvider
from squid.suggestions.infrastructure.providers.taxonomy import PendingTagProvider, TaxonomyProvider
from squid.suggestions.infrastructure.providers.versions import VersionProvider

__all__ = [
    "CallableProvider",
    "DocumentProvider",
    "PendingTagProvider",
    "SearchFieldProvider",
    "SearchSortProvider",
    "StaticProvider",
    "TaxonomyProvider",
    "VersionProvider",
]
