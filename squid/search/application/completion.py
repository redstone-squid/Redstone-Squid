"""Find the part of a query being edited so the UI can offer completions.

Incomplete input such as `restriction:` fails full query parsing but still needs
suggestions. This module examines the text before the caret without validating
the whole query. The result identifies a field value, range, or bare term and
the span that a selected completion should replace.
"""

from dataclasses import dataclass
from enum import StrEnum

from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldDefinition, FieldRegistry, FieldType

_TOKEN_BREAKS = frozenset(" \t\n()[]")
"""Token boundaries outside quotes; field operators stay within the token."""

_OPERATORS = (":", "<=", ">=", "<", ">")
"""Check two-character comparisons before their one-character forms."""

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
    """Exclusive end of the replacement span `query[start:end]`."""

    field: FieldDefinition | None = None
    quoted: bool = False
    """Whether the value being completed opened a quote that must be closed."""


def analyze(
    query: str,
    cursor: int | None = None,
    registry: FieldRegistry = DEFAULT_FIELD_REGISTRY,
) -> CompletionContext:
    """Describe the token before `cursor`, defaulting to the end of the query.

    Cursor positions outside the string are clamped to its bounds. The replacement
    span ends at the cursor, so any text after it is left for the caller to handle.
    """
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
    """Return whether the field supports fixed or indexed text completions.

    Other types use `range_hint` to explain their accepted syntax.
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
    """Quote a completion containing whitespace or field and grouping punctuation.

    Embedded quotes and backslashes are escaped inside the quoted value. Otherwise,
    a name such as `slime door` would become two search terms. This does not yet
    quote bare query keywords or unsupported modifiers, so it is not a general
    query-literal encoder.
    """
    if quoted or any(character in value for character in ' \t()[]:<>"'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _token_start(query: str, position: int) -> int:
    """Find where the token under the caret begins.

    Whitespace inside an open quote belongs to the value. For example,
    `creator:"Red Stone` is one token even though the name contains a space.
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
    """Return the registered field and the offset where its value begins.

    Return `(None, 0)` for bare terms and unknown field names. Unknown names receive
    field-name suggestions rather than value suggestions.
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
