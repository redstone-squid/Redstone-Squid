"""Application services for cross-surface suggestions."""

from squid.suggestions.application.matching import Candidate, MatchTier, rank
from squid.suggestions.application.ports import (
    ComposedSuggestionProvider,
    SuggestionAuthorizer,
    SuggestionProvider,
)
from squid.suggestions.application.registry import (
    SuggestionRegistry,
    SuggestionSource,
    UnknownSuggestionSourceError,
)
from squid.suggestions.application.services import SuggestionService, content_revision

__all__ = [
    "Candidate",
    "ComposedSuggestionProvider",
    "MatchTier",
    "SuggestionAuthorizer",
    "SuggestionProvider",
    "SuggestionRegistry",
    "SuggestionService",
    "SuggestionSource",
    "UnknownSuggestionSourceError",
    "content_revision",
    "rank",
]
