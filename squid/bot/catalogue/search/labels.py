"""Readable labels for search fields, filters, and sort orders.

The catalogue shows a query as a list of filters rather than as query syntax. For
example, `width<=5 NOT restriction:observerless` is shown as the filters
"Width ≤ 5" and "Not: Restriction is observerless", and the sort `-created_at`
is shown as "Created, newest first".
"""

from datetime import date, datetime
from decimal import Decimal

import squid_ui as sl
from squid.bot.ui import tr, truncate_display_text
from squid.core.search.domain.fields import FieldDefinition, FieldType
from squid.core.search.domain.query import (
    ComparisonOperator,
    FieldExpression,
    NotExpression,
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


def field_label(name: str) -> sl.TextLike:
    """Return the display name of a search field, such as "Record criterion" for `criterion`."""
    match name:
        case "title":
            return tr(t"search-field-title = Title")
        case "description":
            return tr(t"search-field-description = Description")
        case "tag":
            return tr(t"search-field-tag = Tag")
        case "restriction":
            return tr(t"search-field-restriction = Restriction")
        case "type":
            return tr(t"search-field-type = Type")
        case "pattern":
            return tr(t"search-field-pattern = Pattern")
        case "creator":
            return tr(t"search-field-creator = Creator")
        case "version":
            return tr(t"search-field-version = Version")
        case "status":
            return tr(t"search-field-status = Status")
        case "kind":
            return tr(t"search-field-kind = Kind")
        case "criterion":
            return tr(t"search-field-base-title = Record criterion")
        case "version_scope":
            return tr(t"search-field-version-scope = Version scope")
        case "width":
            return tr(t"search-field-width = Width")
        case "height":
            return tr(t"search-field-height = Height")
        case "depth":
            return tr(t"search-field-depth = Depth")
        case "volume":
            return tr(t"search-field-volume = Volume")
        case "completion_at":
            return tr(t"search-field-completion-at = Completed")
        case "created_at":
            return tr(t"search-field-created-at = Submitted")
        case "updated_at":
            return tr(t"search-field-updated-at = Updated")
        case _:
            return name.replace("_", " ").capitalize()


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


def filter_label(clause: QueryExpression) -> sl.TextLike:
    """Describe one editable filter.

    Accepts the leaves returned by `query_leaves`: a field comparison, keywords, or
    either of them negated. Other expressions fall back to their query text.
    """
    if isinstance(clause, NotExpression):
        inner = filter_label(clause.operand)
        return tr(t"search-filter-not = Not: {inner}")
    if isinstance(clause, TextExpression):
        words = truncate_display_text(clause.value, _VALUE_LIMIT)
        return tr(t"search-filter-keywords = Keywords: {words}")
    if isinstance(clause, FieldExpression):
        return _comparison_label(clause.field, clause.operator, clause.value)
    return type(clause).__name__


def _comparison_label(
    field: FieldDefinition, operator: ComparisonOperator, value: ScalarValue | RangeValue
) -> sl.TextLike:
    name = field_label(field.name)
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


def sort_label(sort: str) -> sl.TextLike:
    """Describe a sort value in the `field` or `-field` form, or the default order for an empty one."""
    if not sort:
        return tr(t"search-sort-default = Default order")
    descending = sort.startswith("-")
    name = sort.removeprefix("-")
    field = field_label(name)
    if name.endswith("_at"):
        if descending:
            return tr(t"search-sort-newest = {field}, newest first")
        return tr(t"search-sort-oldest = {field}, oldest first")
    if descending:
        return tr(t"search-sort-descending = {field}, largest first")
    return tr(t"search-sort-ascending = {field}, smallest first")
