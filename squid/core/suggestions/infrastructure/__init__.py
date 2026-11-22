"""Adapters and the registered source catalogue for suggestions."""

from squid.core.suggestions.infrastructure.cache import TtlCache
from squid.core.suggestions.infrastructure.catalogue import build_registry
from squid.core.suggestions.infrastructure.repository import (
    DocumentEntry,
    PostgresSuggestionRepository,
    TagEntry,
)

__all__ = [
    "DocumentEntry",
    "PostgresSuggestionRepository",
    "TagEntry",
    "TtlCache",
    "build_registry",
]
