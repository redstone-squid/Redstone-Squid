"""Public search domain API."""

from squid.search.domain.models import (
    BuildSearchHit,
    CursorPosition,
    MetadataSearchHit,
    RecordSearchHit,
    SearchHit,
    SearchMode,
    SearchPage,
    SearchRequest,
    SearchScope,
    SearchSort,
    SortDirection,
)
from squid.search.domain.query import (
    BooleanExpression,
    BooleanOperator,
    ComparisonOperator,
    FieldExpression,
    NotExpression,
    QueryExpression,
    RangeValue,
    SearchQuery,
    TextExpression,
)

__all__ = [
    "BooleanExpression",
    "BooleanOperator",
    "BuildSearchHit",
    "ComparisonOperator",
    "CursorPosition",
    "FieldExpression",
    "MetadataSearchHit",
    "NotExpression",
    "QueryExpression",
    "RangeValue",
    "RecordSearchHit",
    "SearchHit",
    "SearchMode",
    "SearchPage",
    "SearchQuery",
    "SearchRequest",
    "SearchScope",
    "SearchSort",
    "SortDirection",
    "TextExpression",
]
