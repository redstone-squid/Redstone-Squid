"""Cross-surface suggestions.

One named source registry backs Discord autocomplete, HTTP typeahead, and Minecraft command
completion, so a value that means the same thing on every surface is completed the same way.
"""

from squid.core.suggestions.application import (
    SuggestionRegistry,
    SuggestionService,
    SuggestionSource,
    UnknownSuggestionSourceError,
)
from squid.core.suggestions.domain import (
    MAX_QUERY_SUGGESTIONS,
    ReplacementSpan,
    SourceKind,
    Suggestion,
    SuggestionRequest,
    SuggestionResult,
    ValueType,
    Visibility,
)

__all__ = [
    "MAX_QUERY_SUGGESTIONS",
    "ReplacementSpan",
    "SourceKind",
    "Suggestion",
    "SuggestionRegistry",
    "SuggestionRequest",
    "SuggestionResult",
    "SuggestionService",
    "SuggestionSource",
    "UnknownSuggestionSourceError",
    "ValueType",
    "Visibility",
]
