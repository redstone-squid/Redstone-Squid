"""Adapters and the registered source catalogue for suggestions."""

from squid.suggestions.infrastructure.cache import TtlCache
from squid.suggestions.infrastructure.catalogue import build_registry
from squid.suggestions.infrastructure.repository import (
    DocumentEntry,
    PostgresSuggestionRepository,
    TaxonomyEntry,
)

__all__ = [
    "DocumentEntry",
    "PostgresSuggestionRepository",
    "TaxonomyEntry",
    "TtlCache",
    "build_registry",
]
