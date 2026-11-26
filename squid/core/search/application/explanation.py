"""Explain which values of a document satisfied a query.

The explainer evaluates a parsed query against a document's field values with the
compiler's rules: bare words match any field that bare text searches, a field
clause only that field's own values, words may match the search's close spellings
outside NOT, phrases stay within one value, and keywords compare whole values.

Holder measurements follow SQL's three-valued logic, because the compiler reads them
through scalar subqueries: an absent measurement is unknown, and so is its negation.
A record with holder clauses matches through its first holder that satisfies the
whole query, or else through its category alone.

The SQL predicate decides what matches; the explainer only reports why. A property
test checks that the two agree.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from whenever import Instant

from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.core.search.domain import (
    BooleanExpression,
    BooleanOperator,
    ComparisonOperator,
    FieldExpression,
    FieldType,
    NotExpression,
    PresenceExpression,
    QueryExpression,
    RangeValue,
    SearchQuery,
    TextExpression,
    TextMatch,
)
from squid.core.search.domain.evidence import FieldMatch, FieldValue, SearchEvidence
from squid.core.search.domain.query import has_holder_clauses, typed_value
from squid.core.search.domain.text import TermExpansion, normalize_text, terms, tokenize

type _Truth = bool | None
"""True, false, or SQL's unknown."""

_EXACT = TermExpansion()


def explain(
    query: SearchQuery,
    values: Sequence[FieldValue],
    *,
    record: bool,
    expansion: TermExpansion = _EXACT,
    registry: FieldRegistry = DEFAULT_FIELD_REGISTRY,
) -> SearchEvidence | None:
    """Return the evidence that a document matches `query`, or None if it does not."""
    if query.expression is None:
        return SearchEvidence()
    if record and has_holder_clauses(query):
        holders = [value.holder_build_id for value in values if value.field == "holder"]
        for holder in holders:
            if holder is None:
                continue
            truth, matches = _Explainer(values, expansion, registry, on_record=True, holder=holder).evaluate(
                query.expression
            )
            if truth is True:
                return SearchEvidence(matches)
        explainer = _Explainer(values, expansion, registry, on_record=True)
    else:
        explainer = _Explainer(values, expansion, registry)
    truth, matches = explainer.evaluate(query.expression)
    return SearchEvidence(matches) if truth is True else None


@dataclass(frozen=True, slots=True)
class _Explainer:
    values: Sequence[FieldValue]
    expansion: TermExpansion
    registry: FieldRegistry
    on_record: bool = False
    holder: int | None = None

    def evaluate(self, expression: QueryExpression, *, negated: bool = False) -> tuple[_Truth, tuple[FieldMatch, ...]]:
        if isinstance(expression, NotExpression):
            truth, _ = self.evaluate(expression.operand, negated=not negated)
            return (None if truth is None else not truth), ()
        if isinstance(expression, BooleanExpression):
            results = [self.evaluate(operand, negated=negated) for operand in expression.operands]
            truths = [truth for truth, _ in results]
            if expression.operator is BooleanOperator.AND:
                truth: _Truth = False if False in truths else None if None in truths else True
                evidence = [match for _, matches in results for match in matches] if truth else []
            else:
                truth = True if True in truths else None if None in truths else False
                evidence = [match for result, matches in results if result is True for match in matches]
            return truth, tuple(evidence)
        expansion = _EXACT if negated else self.expansion
        if isinstance(expression, TextExpression):
            texts = [value for value in self._texts() if self._free_text(value)]
            return _words(texts, expression.value, phrase=expression.phrase, expansion=expansion)
        if self.on_record and expression.field.holder_scoped:
            return self._measurement(expression)
        if isinstance(expression, PresenceExpression):
            present = any(value.field == expression.field.storage for value in self._own())
            return present is expression.is_set, ()
        return self._field(expression, expansion)

    def _field(self, expression: FieldExpression, expansion: TermExpansion) -> tuple[_Truth, tuple[FieldMatch, ...]]:
        field = expression.field
        storage, stored = expression.stored()
        own = [value for value in self._own() if value.field == storage]
        if field.value_type is FieldType.TEXT:
            if expression.operator is not ComparisonOperator.EQUAL or isinstance(stored, RangeValue):
                return False, ()
            texts = [value for value in own if isinstance(value.value, str)]
            if field.text_match is TextMatch.WORDS:
                return _words(texts, str(stored), phrase=expression.phrase, expansion=expansion)
            wanted = normalize_text(str(stored))
            matches = tuple(
                FieldMatch(value.field, text, ((0, len(text)),) if text else ())
                for value in texts
                if normalize_text(text := str(value.value)) == wanted
            )
            return bool(matches), matches
        matches = tuple(
            FieldMatch(value.field, str(value.value))
            for value in own
            if _compare(value.value, replace(expression, value=stored), field.value_type) is True
        )
        return bool(matches), matches

    def _measurement(self, expression: FieldExpression | PresenceExpression) -> tuple[_Truth, tuple[FieldMatch, ...]]:
        if self.holder is None:
            return None, ()
        measured = next(
            (
                value
                for value in self.values
                if value.holder_build_id == self.holder and value.field == expression.field.storage
            ),
            None,
        )
        if isinstance(expression, PresenceExpression):
            return (measured is not None) is expression.is_set, ()
        if measured is None:
            return None, ()
        truth = _compare(measured.value, expression, expression.field.value_type)
        return truth, ((FieldMatch(measured.field, str(measured.value), holder_build_id=self.holder),) if truth else ())

    def _free_text(self, value: FieldValue) -> bool:
        definition = self.registry.stored(value.field)
        return definition is not None and definition.free_text is not None

    def _texts(self) -> list[FieldValue]:
        return [value for value in self.values if isinstance(value.value, str)]

    def _own(self) -> list[FieldValue]:
        return [value for value in self.values if value.holder_build_id is None]


def _words(
    values: Sequence[FieldValue], text: str, *, phrase: bool, expansion: TermExpansion
) -> tuple[_Truth, tuple[FieldMatch, ...]]:
    """Match words anywhere among the values, or a phrase within one value."""
    wanted = terms(text)
    if not wanted:
        return True, ()
    matches: list[FieldMatch] = []
    found: set[int] = set()
    for value in values:
        assert isinstance(value.value, str)
        tokens = tokenize(value.value)
        spans: list[tuple[int, int]] = []
        close = False
        if phrase:
            size = len(wanted)
            for start in range(len(tokens) - size + 1):
                if tuple(token.term for token in tokens[start : start + size]) == wanted:
                    spans.extend((token.start, token.end) for token in tokens[start : start + size])
                    found.add(0)
        else:
            for index, word in enumerate(wanted):
                accepted = expansion.matches(word)
                for token in tokens:
                    if token.term in accepted:
                        spans.append((token.start, token.end))
                        found.add(index)
                        close = close or token.term != word
        if spans:
            matches.append(
                FieldMatch(value.field, value.value, tuple(sorted(set(spans))), value.holder_build_id, close)
            )
    satisfied = bool(found) if phrase else len(found) == len(wanted)
    return satisfied, (tuple(matches) if satisfied else ())


def _compare(value: object, expression: FieldExpression, field_type: FieldType) -> _Truth:
    """Compare a stored value with a clause as SQL would.

    The stored value and the clause's value both have the field's type, which the
    checker cannot see through the unions; hence the suppressions below.
    """
    if not isinstance(value, Decimal | Instant | bool | str):
        return None
    target = expression.value
    if isinstance(target, RangeValue):
        lower, upper = typed_value(target.lower, field_type), typed_value(target.upper, field_type)
        return lower <= value <= upper  # type: ignore[operator]
    compared = typed_value(target, field_type)
    match expression.operator:
        case ComparisonOperator.EQUAL:
            return value == compared
        case ComparisonOperator.LESS_THAN:
            return value < compared  # type: ignore[operator]
        case ComparisonOperator.LESS_THAN_OR_EQUAL:
            return value <= compared  # type: ignore[operator]
        case ComparisonOperator.GREATER_THAN:
            return value > compared  # type: ignore[operator]
        case ComparisonOperator.GREATER_THAN_OR_EQUAL:
            return value >= compared  # type: ignore[operator]
