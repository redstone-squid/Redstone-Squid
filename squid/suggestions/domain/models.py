"""Transport-neutral suggestion requests and results.

A suggestion is one candidate completion offered while a user is still typing, on any surface:
a Discord autocomplete choice, an HTTP typeahead entry, or a Brigadier completion. Every surface
speaks these values so ranking, limits, and visibility are decided once.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

MAX_SUGGESTIONS = 25
"""Hard ceiling on returned candidates, set by Discord's autocomplete response limit."""

MAX_QUERY_LENGTH = 200
"""Longest partial input a provider is asked to match, past which typing is not a prefix search."""


class SourceKind(StrEnum):
    """How a source produces candidates."""

    ENUMERABLE = "enumerable"
    """The full candidate set is small enough to list, cache, and revision."""

    QUERIED = "queried"
    """Candidates are selected per query and cannot be enumerated."""


class Visibility(StrEnum):
    """Who may read a source's candidates."""

    PUBLIC = "public"
    """Data already published to anonymous callers."""

    REQUIRES_NODE = "requires_node"
    """Data gated behind the source's permission node."""

    VIEWER_SCOPED = "viewer_scoped"
    """Data belonging to the requesting account, filtered by the provider."""


class ValueType(StrEnum):
    """The scalar type a suggestion's value carries."""

    STRING = "string"
    INTEGER = "integer"


@dataclass(frozen=True, slots=True)
class SuggestionViewer:
    """The identity a suggestion request is answered for.

    Carries only what a provider may filter on. Permission decisions are made through a
    `SuggestionAuthorizer` rather than by inspecting this value, because each transport resolves
    them differently.
    """

    account_id: int | None = None
    guild_id: int | None = None


@dataclass(frozen=True, slots=True)
class ReplacementSpan:
    """The half-open range of the input a suggestion's value replaces.

    Sources that complete part of a larger string (a query-language token, one entry in a
    comma-separated list) report the span so a client splices instead of clobbering the whole
    input. `None` means the value replaces everything.
    """

    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One candidate completion."""

    value: str
    """What is submitted when the candidate is picked."""

    label: str
    """What is shown to the user."""

    description: str | None = None
    """Secondary text disambiguating candidates that share a label."""

    kind: str = ""
    """The entity type behind the candidate, so mixed result lists stay readable."""


@dataclass(frozen=True, slots=True)
class SuggestionRequest:
    """A normalized request for completions of a partially typed value."""

    source: str
    query: str = ""
    limit: int = MAX_SUGGESTIONS
    context: Mapping[str, str] = field(default_factory=dict)
    """Source-specific scoping, such as the build category an option set belongs to."""

    locale: str | None = None
    viewer: SuggestionViewer = SuggestionViewer()
    cursor: int | None = None
    """Caret offset within `query`, for sources that complete mid-string."""


@dataclass(frozen=True, slots=True)
class SuggestionResult:
    """Ranked candidates and the caching and splicing metadata a client needs."""

    items: tuple[Suggestion, ...] = ()
    revision: int | None = None
    """Content revision of an enumerable source, for `ETag` and client-side caching."""

    replacement: ReplacementSpan | None = None
