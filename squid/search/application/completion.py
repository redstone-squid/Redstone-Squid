"""Cursor-aware analysis of a partially typed search query.

`SearchQueryParser` cannot help here. It calls `_fail()` on the first token it cannot complete,
and a query being typed is *always* in that state — `restriction:` is a syntax error right up to
the moment it stops needing completion. So this re-reads the same token shapes without ever
raising, and reports what the caret is sitting in.

It deliberately looks only at the token under the caret rather than parsing the whole query. What
may be suggested at a position depends on that token and, at most, the field name in front of it;
whether the rest of the query is well-formed is not this module's problem.
"""

from dataclasses import dataclass
from enum import StrEnum

from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldDefinition, FieldRegistry, FieldType

_TOKEN_BREAKS = frozenset(" \t\n()[]")
"""Characters that end a token. Mirrors the parser's word breaks, minus the ones a caret sits in."""

_OPERATORS = (":", "<=", ">=", "<", ">")
"""Longest first, so `<=` is not read as `<` followed by a stray `=`."""

BOOLEAN_KEYWORDS = ("AND", "OR", "NOT")


class CompletionKind(StrEnum):
    """What the caret is positioned to complete."""

    TERM = "term"
    """A bare word: a field name, a boolean keyword, or free search text."""

    FIELD_VALUE = "field_value"
    """The value half of a `field:value` expression."""

    RANGE = "range"
    """Inside a `[low TO high]` range, where only syntax can be suggested."""


@dataclass(frozen=True, slots=True)
class CompletionContext:
    """What the caret sits in, and the span a chosen value replaces."""

    kind: CompletionKind
    prefix: str
    """What has been typed for the token being completed."""

    start: int
    end: int
    """The half-open span to replace, so a client splices instead of clobbering."""

    field: FieldDefinition | None = None
    quoted: bool = False
    """Whether the value being completed opened a quote that must be closed."""


def analyze(
    query: str,
    cursor: int | None = None,
    registry: FieldRegistry = DEFAULT_FIELD_REGISTRY,
) -> CompletionContext:
    """Describe what may be completed at `cursor`, defaulting to the end of the query."""
    position = len(query) if cursor is None else max(0, min(cursor, len(query)))
    if (open_bracket := _open_range(query, position)) is not None:
        # A range spans whitespace, so it has to be detected before tokenizing. Only syntax can be
        # suggested inside one; the field it belongs to is whatever opened the bracket.
        field = _field_before(query, open_bracket, registry)
        start = _token_start(query, position)
        return CompletionContext(CompletionKind.RANGE, query[start:position], start, position, field)

    start = _token_start(query, position)
    token = query[start:position]

    field, operator_end = _split_field(token, registry)
    if field is None:
        return CompletionContext(CompletionKind.TERM, token, start, position)

    value = token[operator_end:]
    quoted = value.startswith('"')
    return CompletionContext(
        CompletionKind.FIELD_VALUE,
        value.removeprefix('"'),
        start + operator_end,
        position,
        field,
        quoted=quoted,
    )


def completes_values(field: FieldDefinition) -> bool:
    """Whether listing indexed values makes sense for a field.

    Numbers and timestamps have no useful value list: nobody wants the twelve hundred widths that
    happen to be indexed. Those get syntax help from `range_hint` instead.
    """
    return field.value_type is FieldType.TEXT


def range_hint(field: FieldDefinition) -> str | None:
    """Suggest the comparison syntax a non-text field supports."""
    if field.value_type is FieldType.BOOLEAN:
        return "true or false"
    if not field.supports_range:
        return None
    return "a value, or [low TO high]"


def render_value(value: str, *, quoted: bool) -> str:
    """Render a chosen value so it parses back as one token.

    A value containing a space has to be quoted or the parser reads the tail as a separate term,
    which silently widens the search instead of narrowing it.
    """
    if quoted or any(character in value for character in ' \t()[]:<>"'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _token_start(query: str, position: int) -> int:
    """Find where the token under the caret begins.

    An open quote wins over whitespace: inside `creator:"Nu`, the space in a name being typed is
    part of the value, not the end of it.
    """
    if (quote := _open_quote(query, position)) is not None:
        return _token_start(query, quote)
    start = position
    while start > 0 and query[start - 1] not in _TOKEN_BREAKS:
        start -= 1
    return start


def _open_quote(query: str, position: int) -> int | None:
    """Return the offset of an unclosed quote before the caret, if there is one."""
    opened: int | None = None
    index = 0
    while index < position:
        character = query[index]
        if character == "\\":
            index += 2
            continue
        if character == '"':
            opened = None if opened is not None else index
        index += 1
    return opened


def _open_range(query: str, position: int) -> int | None:
    """Return the offset of an unclosed `[` before the caret, if there is one."""
    for index in range(position - 1, -1, -1):
        if query[index] == "]":
            return None
        if query[index] == "[":
            return index
    return None


def _field_before(query: str, bracket: int, registry: FieldRegistry) -> FieldDefinition | None:
    """Resolve the field whose operator immediately precedes an opening bracket."""
    head = query[:bracket]
    for operator in _OPERATORS:
        if head.endswith(operator):
            name = head[: -len(operator)]
            return registry.resolve(name[_token_start(name, len(name)) :])
    return None


def _split_field(token: str, registry: FieldRegistry) -> tuple[FieldDefinition | None, int]:
    """Find the field an operator in `token` names, and where its value begins.

    Returns `(None, 0)` when the token is not a field expression, including when it names something
    the registry does not publish — an unknown field is completed as a field name, not as a value
    of a field that does not exist.
    """
    for operator in _OPERATORS:
        head, separator, _ = token.partition(operator)
        if not separator:
            continue
        field = registry.resolve(head)
        if field is None:
            return None, 0
        return field, len(head) + len(operator)
    return None, 0
