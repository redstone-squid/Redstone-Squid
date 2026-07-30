"""Public search application API."""

from squid.search.application.cursor import CursorCodec, InvalidCursorError
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldDefinition, FieldRegistry, FieldType
from squid.search.application.parser import QuerySyntaxError, SearchQueryParser
from squid.search.application.ports import SearchQueryCompiler
from squid.search.application.query_analysis import is_filter_only, positive_text_expressions
from squid.search.application.ranking import (
    DEFAULT_RRF_WEIGHTS,
    RankedCandidate,
    RankingBranch,
    SearchDocumentOrder,
    reciprocal_rank_fusion,
    sort_filter_only,
)

__all__ = [
    "DEFAULT_FIELD_REGISTRY",
    "DEFAULT_RRF_WEIGHTS",
    "CursorCodec",
    "FieldDefinition",
    "FieldRegistry",
    "FieldType",
    "InvalidCursorError",
    "QuerySyntaxError",
    "RankedCandidate",
    "RankingBranch",
    "SearchDocumentOrder",
    "SearchQueryCompiler",
    "SearchQueryParser",
    "is_filter_only",
    "positive_text_expressions",
    "reciprocal_rank_fusion",
    "sort_filter_only",
]
