"""Readable labels for search filters.

The catalogue shows a query as a list of filters rather than as query syntax. For
example, `width<=5 NOT restriction:observerless` is shown as the filters
"Width ≤ 5" and "Not: Restriction is observerless". Field and sort labels come
from the field registry.
"""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

import squid_ui as sl
from squid.bot.ui import tr, truncate_display_text
from squid.core.search.domain.fields import FieldDefinition, FieldType
from squid.core.search.domain.query import (
    CREATOR_ID_STORAGE,
    ComparisonOperator,
    FieldExpression,
    NotExpression,
    PresenceExpression,
    QueryExpression,
    RangeValue,
    ScalarValue,
    TextExpression,
)
from squid_i18n import Text

# Keeps a filter label within Discord's 80-character button limit, together with its field name.
_VALUE_LIMIT = 40

_SYMBOLS = {
    ComparisonOperator.LESS_THAN: "<",
    ComparisonOperator.LESS_THAN_OR_EQUAL: "≤",
    ComparisonOperator.GREATER_THAN: ">",
    ComparisonOperator.GREATER_THAN_OR_EQUAL: "≥",
}


def value_label(value: ScalarValue | date | datetime) -> str:
    """Format a filter value without query quoting, such as `observerless` or `2025-01-01`."""
    match value:
        case bool():
            return "yes" if value else "no"
        case Decimal():
            text = format(value.normalize(), "f")
        case datetime():
            text = value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(" ", "minutes")
        case date():
            text = value.isoformat()
        case _:
            text = str(value).replace("_", " ")
    return truncate_display_text(text, _VALUE_LIMIT)


def filter_label(clause: QueryExpression, creators: Mapping[str, str] | None = None) -> sl.TextLike:
    """Describe one editable filter.

    Accepts the leaves returned by `query_leaves`: a field comparison, keywords, or
    either of them negated. Other expressions fall back to their query text.
    `creators` maps public creator IDs to names, shown instead of the ID.
    """
    if isinstance(clause, NotExpression):
        inner = filter_label(clause.operand, creators)
        return tr(t"search-filter-not = Not: {inner}")
    if isinstance(clause, TextExpression):
        words = truncate_display_text(clause.value, _VALUE_LIMIT)
        return tr(t"search-filter-keywords = Keywords: {words}")
    if isinstance(clause, PresenceExpression):
        name = clause.field.display_name
        if clause.is_set:
            return tr(t"search-filter-is-set = {name} is set")
        return tr(t"search-filter-is-not-set = {name} is not set")
    if isinstance(clause, FieldExpression):
        storage, value = clause.stored()
        if storage == CREATOR_ID_STORAGE and creators and (creator := creators.get(str(value))) is not None:
            name = clause.field.display_name
            shown = truncate_display_text(creator, _VALUE_LIMIT)
            return tr(t"search-filter-equals = {name} is {shown}")
        return _comparison_label(clause.field, clause.operator, clause.value)
    return type(clause).__name__


def _comparison_label(
    field: FieldDefinition, operator: ComparisonOperator, value: ScalarValue | RangeValue
) -> sl.TextLike:
    name = field.display_name
    if isinstance(value, RangeValue):
        lower = value_label(value.lower)
        upper = value_label(value.upper)
        return tr(t"search-filter-range = {name} {lower} to {upper}")
    shown = value_label(value)
    if operator is ComparisonOperator.EQUAL:
        if field.value_type is FieldType.TEXT and field.name in {"title", "description"}:
            return tr(t"search-filter-contains = {name} has “{shown}”")
        return tr(t"search-filter-equals = {name} is {shown}")
    symbol = _SYMBOLS[operator]
    return Text(t"{name} {symbol} {shown}")
