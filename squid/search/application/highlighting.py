"""Conservative title highlighting independent of ranking and output markup."""

import re
import unicodedata

from squid.search.domain import (
    BooleanExpression,
    BooleanOperator,
    ComparisonOperator,
    FieldExpression,
    NotExpression,
    QueryExpression,
    SearchQuery,
    SearchText,
    TextExpression,
)


def highlight_title(title: str, query: SearchQuery) -> SearchText:
    """Highlight literal title terms without claiming to explain fuzzy ranking.

    OR and NOT subtrees are omitted because matching a word alone cannot establish
    which branch admitted the document. Field-qualified terms apply only to title.
    """
    terms = _title_terms(query.expression)
    normalized, offsets = _normalize(title)
    spans: list[tuple[int, int]] = []
    for term in terms:
        needle, _ = _normalize(term)
        if not needle:
            continue
        spans.extend(
            (offsets[match.start()], offsets[match.end() - 1] + 1)
            for match in re.finditer(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", normalized)
        )
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return SearchText(title, tuple(merged))


def _title_terms(expression: QueryExpression | None) -> tuple[str, ...]:
    if isinstance(expression, TextExpression):
        return (expression.value,) if expression.phrase else tuple(expression.value.split())
    if (
        isinstance(expression, FieldExpression)
        and expression.field == "title"
        and expression.operator is ComparisonOperator.EQUAL
    ):
        value = str(expression.value)
        return (value,) if expression.phrase else tuple(value.split())
    if isinstance(expression, BooleanExpression) and expression.operator is BooleanOperator.AND:
        # Do not use flattened ranking terms: OR and negated branches carry different meaning.
        return tuple(term for operand in expression.operands for term in _title_terms(operand))
    if isinstance(expression, NotExpression):
        return ()
    return ()


def _normalize(value: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(value):
        for normalized in unicodedata.normalize("NFKD", character.casefold().replace("\N{MULTIPLICATION SIGN}", "x")):
            if unicodedata.combining(normalized):
                continue
            if normalized.isspace():
                normalized = " "
                if characters and characters[-1] == " ":
                    continue
            characters.append(normalized)
            offsets.append(index)
    return "".join(characters), offsets
