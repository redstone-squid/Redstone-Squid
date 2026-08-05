"""Safe parser for the supported Lucene-style search subset."""

from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn

from squid.core.errors import ErrorCode, ValidationError
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldDefinition, FieldRegistry
from squid.search.domain.query import (
    BooleanExpression,
    BooleanOperator,
    ComparisonOperator,
    FieldExpression,
    NotExpression,
    QueryExpression,
    RangeValue,
    ScalarValue,
    SearchQuery,
    TextExpression,
)


class QuerySyntaxError(ValidationError):
    """A user-correctable query error with source location and suggestions."""

    def __init__(self, message: str, position: int, *, suggestions: tuple[str, ...] = ()) -> None:
        super().__init__(
            message,
            code=ErrorCode.INVALID_QUERY,
            public_context={"position": position, "suggestions": suggestions},
        )
        self.message = message
        self.position = position
        self.suggestions = suggestions


class _TokenKind(StrEnum):
    WORD = "word"
    PHRASE = "phrase"
    AND = "and"
    OR = "or"
    NOT = "not"
    TO = "to"
    LEFT_PAREN = "left_paren"
    RIGHT_PAREN = "right_paren"
    LEFT_BRACKET = "left_bracket"
    RIGHT_BRACKET = "right_bracket"
    COLON = "colon"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    END = "end"


@dataclass(frozen=True, slots=True)
class _Token:
    kind: _TokenKind
    value: str
    position: int


_PUNCTUATION = {
    "(": _TokenKind.LEFT_PAREN,
    ")": _TokenKind.RIGHT_PAREN,
    "[": _TokenKind.LEFT_BRACKET,
    "]": _TokenKind.RIGHT_BRACKET,
    ":": _TokenKind.COLON,
}
_UNSUPPORTED_MODIFIERS = frozenset("*?~^")
_WORD_BREAKS = frozenset('()[]:<>"*?~^')


class SearchQueryParser:
    """Parse an allowlisted, bounded Lucene-style query language."""

    def __init__(
        self,
        registry: FieldRegistry = DEFAULT_FIELD_REGISTRY,
        *,
        max_length: int = 1_000,
        max_tokens: int = 100,
        max_nesting: int = 10,
    ) -> None:
        self._registry = registry
        self._max_length = max_length
        self._max_tokens = max_tokens
        self._max_nesting = max_nesting
        self._tokens: tuple[_Token, ...] = ()
        self._index = 0

    def parse(self, source: str) -> SearchQuery:
        """Parse source into a persistence-neutral syntax tree."""
        if len(source) > self._max_length:
            _fail(f"query exceeds {self._max_length} characters", self._max_length)
        self._tokens = self._tokenize(source)
        self._index = 0
        if self._peek().kind is _TokenKind.END:
            return SearchQuery(expression=None, normalized="")
        expression = self._parse_or(nesting=0)
        trailing = self._peek()
        if trailing.kind is not _TokenKind.END:
            _fail(f"unexpected {trailing.value!r}", trailing.position)
        return SearchQuery(expression=expression, normalized=_render(expression))

    def _tokenize(self, source: str) -> tuple[_Token, ...]:
        tokens: list[_Token] = []
        index = 0
        while index < len(source):
            character = source[index]
            if character.isspace():
                index += 1
                continue
            if character in _UNSUPPORTED_MODIFIERS:
                _fail(f"unsupported query modifier {character!r}", index)
            if character in _PUNCTUATION:
                tokens.append(_Token(_PUNCTUATION[character], character, index))
                index += 1
                continue
            if character in "<>":
                start = index
                index += 1
                inclusive = index < len(source) and source[index] == "="
                if inclusive:
                    index += 1
                kind = {
                    ("<", False): _TokenKind.LESS_THAN,
                    ("<", True): _TokenKind.LESS_THAN_OR_EQUAL,
                    (">", False): _TokenKind.GREATER_THAN,
                    (">", True): _TokenKind.GREATER_THAN_OR_EQUAL,
                }[(character, inclusive)]
                tokens.append(_Token(kind, source[start:index], start))
                continue
            if character == '"':
                phrase, index = self._read_phrase(source, index)
                tokens.append(_Token(_TokenKind.PHRASE, phrase, index - len(phrase) - 2))
                continue
            start = index
            while index < len(source) and not source[index].isspace() and source[index] not in _WORD_BREAKS:
                index += 1
            value = source[start:index]
            keyword = {
                "AND": _TokenKind.AND,
                "OR": _TokenKind.OR,
                "NOT": _TokenKind.NOT,
                "TO": _TokenKind.TO,
            }.get(value.upper())
            tokens.append(_Token(keyword or _TokenKind.WORD, value, start))
            if len(tokens) > self._max_tokens:
                _fail(f"query exceeds {self._max_tokens} tokens", start)
        tokens.append(_Token(_TokenKind.END, "", len(source)))
        return tuple(tokens)

    @staticmethod
    def _read_phrase(source: str, start: int) -> tuple[str, int]:
        value: list[str] = []
        index = start + 1
        while index < len(source):
            character = source[index]
            if character == '"':
                return "".join(value), index + 1
            if character == "\\":
                index += 1
                if index >= len(source) or source[index] not in {'"', "\\"}:
                    _fail("only quotes and backslashes may be escaped", index - 1)
                character = source[index]
            value.append(character)
            index += 1
        return _fail("unterminated quoted phrase", start)

    def _parse_or(self, *, nesting: int) -> QueryExpression:
        expression = self._parse_and(nesting=nesting)
        operands = [expression]
        while self._accept(_TokenKind.OR):
            operands.append(self._parse_and(nesting=nesting))
        return _combine(BooleanOperator.OR, operands)

    def _parse_and(self, *, nesting: int) -> QueryExpression:
        operands = [self._parse_unary(nesting=nesting)]
        while True:
            if self._accept(_TokenKind.AND) or self._starts_expression(self._peek().kind):
                operands.append(self._parse_unary(nesting=nesting))
            else:
                break
        return _combine(BooleanOperator.AND, operands)

    def _parse_unary(self, *, nesting: int) -> QueryExpression:
        if self._accept(_TokenKind.NOT):
            return NotExpression(self._parse_unary(nesting=nesting))
        return self._parse_primary(nesting=nesting)

    def _parse_primary(self, *, nesting: int) -> QueryExpression:
        token = self._peek()
        if self._accept(_TokenKind.LEFT_PAREN):
            if nesting >= self._max_nesting:
                _fail(f"query nesting exceeds {self._max_nesting}", token.position)
            expression = self._parse_or(nesting=nesting + 1)
            self._expect(_TokenKind.RIGHT_PAREN, "expected ')'")
            return expression
        if token.kind not in {_TokenKind.WORD, _TokenKind.PHRASE}:
            _fail("expected a search term", token.position)
        self._advance()
        if token.kind is _TokenKind.WORD and self._peek().kind in {
            _TokenKind.COLON,
            _TokenKind.LESS_THAN,
            _TokenKind.LESS_THAN_OR_EQUAL,
            _TokenKind.GREATER_THAN,
            _TokenKind.GREATER_THAN_OR_EQUAL,
        }:
            return self._parse_field(token)
        return TextExpression(token.value, phrase=token.kind is _TokenKind.PHRASE)

    def _parse_field(self, name: _Token) -> FieldExpression:
        field = self._registry.resolve(name.value)
        if field is None:
            _fail(
                f"unknown search field {name.value!r}",
                name.position,
                suggestions=self._registry.suggestions(name.value),
            )
        operator_token = self._advance()
        operator = {
            _TokenKind.COLON: ComparisonOperator.EQUAL,
            _TokenKind.LESS_THAN: ComparisonOperator.LESS_THAN,
            _TokenKind.LESS_THAN_OR_EQUAL: ComparisonOperator.LESS_THAN_OR_EQUAL,
            _TokenKind.GREATER_THAN: ComparisonOperator.GREATER_THAN,
            _TokenKind.GREATER_THAN_OR_EQUAL: ComparisonOperator.GREATER_THAN_OR_EQUAL,
        }[operator_token.kind]
        if self._accept(_TokenKind.LEFT_BRACKET):
            if operator is not ComparisonOperator.EQUAL:
                _fail("ranges must follow ':'", operator_token.position)
            return self._parse_range(field)
        value_token = self._expect_value()
        if operator is not ComparisonOperator.EQUAL and not field.supports_range:
            _fail(f"{field.name} does not support comparisons", operator_token.position)
        return FieldExpression(
            field.name,
            operator,
            self._coerce(field, value_token),
            phrase=value_token.kind is _TokenKind.PHRASE,
            storage_field=field.storage_name,
            value_type=field.value_type.value if field.storage_name is not None else None,
        )

    def _parse_range(self, field: FieldDefinition) -> FieldExpression:
        if not field.supports_range:
            _fail(f"{field.name} does not support ranges", self._peek().position)
        lower = self._expect_value()
        self._expect(_TokenKind.TO, "expected 'TO' in range")
        upper = self._expect_value()
        self._expect(_TokenKind.RIGHT_BRACKET, "expected ']' after range")
        return FieldExpression(
            field.name,
            ComparisonOperator.EQUAL,
            RangeValue(self._coerce(field, lower), self._coerce(field, upper)),
            storage_field=field.storage_name,
            value_type=field.value_type.value if field.storage_name is not None else None,
        )

    def _coerce(self, field: FieldDefinition, token: _Token) -> ScalarValue:
        try:
            return self._registry.coerce(field, token.value)
        except ValueError as error:
            _fail(str(error), token.position, cause=error)

    def _expect_value(self) -> _Token:
        token = self._peek()
        if token.kind not in {_TokenKind.WORD, _TokenKind.PHRASE}:
            _fail("expected a field value", token.position)
        return self._advance()

    def _expect(self, kind: _TokenKind, message: str) -> _Token:
        token = self._peek()
        if token.kind is not kind:
            _fail(message, token.position)
        return self._advance()

    def _accept(self, kind: _TokenKind) -> bool:
        if self._peek().kind is not kind:
            return False
        self._advance()
        return True

    def _advance(self) -> _Token:
        token = self._tokens[self._index]
        self._index += 1
        return token

    def _peek(self) -> _Token:
        return self._tokens[self._index]

    @staticmethod
    def _starts_expression(kind: _TokenKind) -> bool:
        return kind in {_TokenKind.WORD, _TokenKind.PHRASE, _TokenKind.LEFT_PAREN, _TokenKind.NOT}


def _combine(operator: BooleanOperator, operands: list[QueryExpression]) -> QueryExpression:
    if len(operands) == 1:
        return operands[0]
    flattened: list[QueryExpression] = []
    for operand in operands:
        if isinstance(operand, BooleanExpression) and operand.operator is operator:
            flattened.extend(operand.operands)
        else:
            flattened.append(operand)
    return BooleanExpression(operator, tuple(flattened))


def _render(expression: QueryExpression) -> str:
    if isinstance(expression, TextExpression):
        return _quote(expression.value) if expression.phrase else expression.value
    if isinstance(expression, FieldExpression):
        if isinstance(expression.value, RangeValue):
            value = f"[{_scalar(expression.value.lower)} TO {_scalar(expression.value.upper)}]"
        else:
            value = _quote(str(expression.value)) if expression.phrase else _scalar(expression.value)
        operator = {
            ComparisonOperator.EQUAL: ":",
            ComparisonOperator.LESS_THAN: "<",
            ComparisonOperator.LESS_THAN_OR_EQUAL: "<=",
            ComparisonOperator.GREATER_THAN: ">",
            ComparisonOperator.GREATER_THAN_OR_EQUAL: ">=",
        }[expression.operator]
        return f"{expression.field}{operator}{value}"
    if isinstance(expression, NotExpression):
        operand = _render(expression.operand)
        if isinstance(expression.operand, BooleanExpression):
            operand = f"({operand})"
        return f"NOT {operand}"
    separator = f" {expression.operator.value.upper()} "
    return separator.join(_render_with_parentheses(operand, expression.operator) for operand in expression.operands)


def _render_with_parentheses(expression: QueryExpression, parent: BooleanOperator) -> str:
    rendered = _render(expression)
    if isinstance(expression, BooleanExpression) and expression.operator is not parent:
        return f"({rendered})"
    return rendered


def _quote(value: str) -> str:
    return f'"{value.replace("\\", "\\\\").replace('"', '\\"')}"'


def _scalar(value: ScalarValue) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _fail(
    message: str,
    position: int,
    *,
    suggestions: tuple[str, ...] = (),
    cause: Exception | None = None,
) -> NoReturn:
    raise QuerySyntaxError(message, position, suggestions=suggestions) from cause
