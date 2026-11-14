"""Public search domain API."""

from squid.search.domain.fields import FieldDefinition, FieldType
from squid.search.domain.models import (
    BuildSearchHit,
    MetadataSearchHit,
    RecordSearchHit,
    SearchHit,
    SearchMatch,
    SearchMode,
    SearchPage,
    SearchRequest,
    SearchScope,
    SearchSort,
    SearchText,
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
    "FieldDefinition",
    "FieldExpression",
    "FieldType",
    "MetadataSearchHit",
    "NotExpression",
    "QueryExpression",
    "RangeValue",
    "RecordSearchHit",
    "SearchHit",
    "SearchMatch",
    "SearchMode",
    "SearchPage",
    "SearchQuery",
    "SearchRequest",
    "SearchScope",
    "SearchSort",
    "SearchText",
    "SortDirection",
    "TextExpression",
]
