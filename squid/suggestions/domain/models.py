"""Values shared by Discord autocomplete, HTTP suggestions, and command completion.

Providers use these requests and results without depending on a transport's
response format.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from squid.core.actors import Actor

MAX_SUGGESTIONS = 25
"""Maximum for nonzero request limits, matching Discord's autocomplete response limit."""

MAX_QUERY_LENGTH = 200
"""Maximum query length in Python characters; the service truncates longer input."""


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
class ReplacementSpan:
    """The half-open character range in the query to replace with a selected value.

    For example, `[5, 8)` replaces the three characters at offsets 5 through 7.
    Offsets use Python string indices, not byte offsets.
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
    """A request for completions; the service applies query and result limits."""

    source: str
    query: str = ""
    limit: int = MAX_SUGGESTIONS
    """Maximum results; zero requests all values from a candidate provider."""

    context: Mapping[str, str] = field(default_factory=dict)
    """Source-specific scoping, such as the build category an option set belongs to."""

    locale: str | None = None
    viewer: Actor = Actor()
    cursor: int | None = None
    """Python character offset in `query`; `None` places the caret at the end."""


@dataclass(frozen=True, slots=True)
class SuggestionResult:
    """Ranked candidates and the caching and splicing metadata a client needs."""

    items: tuple[Suggestion, ...] = ()
    revision: int | None = None
    """Hash of the returned values and labels for an enumerable candidate source."""

    replacement: ReplacementSpan | None = None
    """Range to replace; `None` means replace the whole input."""
