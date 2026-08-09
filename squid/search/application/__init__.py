"""Public search application API."""

from squid.search.application.cursor import CursorCodec, InvalidCursorError
from squid.search.application.embeddings import (
    SearchEmbeddingJob,
    SearchEmbeddingModel,
    SearchEmbeddingQueue,
    SearchEmbeddingService,
)
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldDefinition, FieldRegistry, FieldType
from squid.search.application.parser import QuerySyntaxError, SearchQueryParser
from squid.search.application.ports import SearchBackend, SearchQueryCompiler, SearchSlice
from squid.search.application.query_analysis import is_filter_only, positive_text_expressions
from squid.search.application.ranking import (
    DEFAULT_RRF_WEIGHTS,
    RankedCandidate,
    RankingBranch,
    SearchDocumentOrder,
    reciprocal_rank_fusion,
    sort_filter_only,
)
from squid.search.application.services import SearchFieldRegistryProvider, SearchService

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
    "SearchBackend",
    "SearchDocumentOrder",
    "SearchEmbeddingJob",
    "SearchEmbeddingModel",
    "SearchEmbeddingQueue",
    "SearchEmbeddingService",
    "SearchFieldRegistryProvider",
    "SearchQueryCompiler",
    "SearchQueryParser",
    "SearchService",
    "SearchSlice",
    "is_filter_only",
    "positive_text_expressions",
    "reciprocal_rank_fusion",
    "sort_filter_only",
]
