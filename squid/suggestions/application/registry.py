"""The named suggestion source registry.

Source ids extend the `option_source` namespace the submission form manifest already publishes, so
a form field, a Discord parameter, and a Brigadier argument that mean the same thing name the same
thing. Ids keep that namespace's shape so any source can be served as a form option set.
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from squid.core.errors import ErrorCode, InvalidStateError, NotFoundError
from squid.core.i18n import _
from squid.suggestions.application.ports import ComposedSuggestionProvider, SuggestionProvider
from squid.suggestions.domain import SourceKind, ValueType, Visibility

SOURCE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
"""Matches `OptionSource` in the submission API so every source is addressable as a form option."""


class UnknownSuggestionSourceError(NotFoundError):
    """The requested suggestion source is not registered."""

    default_message = "No such suggestion source."
    default_title = "Unknown suggestion source"
    default_code = ErrorCode.NOT_FOUND
    default_resource = "suggestion_source"


@dataclass(frozen=True, slots=True)
class SuggestionSource:
    """What a source is, independent of how it fetches candidates."""

    id: str
    provider: SuggestionProvider | ComposedSuggestionProvider
    kind: SourceKind = SourceKind.QUERIED
    visibility: Visibility = Visibility.PUBLIC
    required_node: str | None = None
    """The permission node checked when `visibility` is `REQUIRES_NODE`."""

    context_keys: frozenset[str] = frozenset()
    """Context this source needs, such as `category` or `guild_id`."""

    value_type: ValueType = ValueType.STRING
    multi_value: str | None = None
    """Separator when the completed parameter holds a list, so callers splice one entry."""

    kind_label: str = ""
    """Default `Suggestion.kind` for candidates that do not set their own."""

    def __post_init__(self) -> None:
        if not SOURCE_ID_PATTERN.match(self.id):
            msg = _("invalid suggestion source id: {source_id!r}")
            raise InvalidStateError(msg, message_params={"source_id": self.id})
        if (self.visibility is Visibility.REQUIRES_NODE) != (self.required_node is not None):
            msg = _("{source_id}: required_node must be set exactly when visibility is requires_node")
            raise InvalidStateError(msg, message_params={"source_id": self.id})


@dataclass(frozen=True, slots=True)
class SuggestionRegistry:
    """Resolve source ids to their definitions."""

    _sources: dict[str, SuggestionSource] = field(default_factory=dict)

    @classmethod
    def of(cls, sources: Iterable[SuggestionSource]) -> SuggestionRegistry:
        """Build a registry, rejecting duplicate ids at construction so typos fail at startup."""
        registered: dict[str, SuggestionSource] = {}
        for source in sources:
            if source.id in registered:
                msg = _("duplicate suggestion source: {source_id}")
                raise InvalidStateError(msg, message_params={"source_id": source.id})
            registered[source.id] = source
        return cls(registered)

    def resolve(self, source_id: str) -> SuggestionSource:
        """Return a registered source or raise."""
        try:
            return self._sources[source_id]
        except KeyError as error:
            raise UnknownSuggestionSourceError(public_context={"source": source_id}) from error

    def get(self, source_id: str) -> SuggestionSource | None:
        """Return a registered source, or `None` when it is not registered."""
        return self._sources.get(source_id)

    def enumerable(self) -> tuple[SuggestionSource, ...]:
        """Return the sources whose full candidate set can be listed and revisioned."""
        return tuple(source for source in self if source.kind is SourceKind.ENUMERABLE)

    def __iter__(self) -> Iterator[SuggestionSource]:
        return iter(self._sources.values())

    def __len__(self) -> int:
        return len(self._sources)

    def __contains__(self, source_id: str) -> bool:
        return source_id in self._sources
