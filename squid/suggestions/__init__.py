"""Cross-surface suggestions.

One named source registry backs Discord autocomplete, HTTP typeahead, and Minecraft command
completion, so a value that means the same thing on every surface is completed the same way.
"""

from squid.suggestions.application import (
    SuggestionRegistry,
    SuggestionService,
    SuggestionSource,
    UnknownSuggestionSourceError,
)
from squid.suggestions.domain import (
    MAX_SUGGESTIONS,
    ReplacementSpan,
    SourceKind,
    Suggestion,
    SuggestionRequest,
    SuggestionResult,
    SuggestionViewer,
    ValueType,
    Visibility,
)

__all__ = [
    "MAX_SUGGESTIONS",
    "ReplacementSpan",
    "SourceKind",
    "Suggestion",
    "SuggestionRegistry",
    "SuggestionRequest",
    "SuggestionResult",
    "SuggestionService",
    "SuggestionSource",
    "SuggestionViewer",
    "UnknownSuggestionSourceError",
    "ValueType",
    "Visibility",
]
