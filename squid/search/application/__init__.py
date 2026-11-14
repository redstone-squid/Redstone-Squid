"""Public search application API."""

from squid.search.application.embeddings import (
    SearchEmbeddingJob,
    SearchEmbeddingModel,
    SearchEmbeddingQueue,
    SearchEmbeddingService,
)
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldDefinition, FieldRegistry, FieldType
from squid.search.application.parser import QuerySyntaxError, SearchQueryParser
from squid.search.application.ports import SearchBackend, SearchSlice
from squid.search.application.query_analysis import is_filter_only, positive_text_expressions
from squid.search.application.ranking import (
    DEFAULT_RRF_WEIGHTS,
    RankedCandidate,
    RankingBranch,
    reciprocal_rank_fusion,
)
from squid.search.application.services import SearchFieldRegistryProvider, SearchService

__all__ = [
    "DEFAULT_FIELD_REGISTRY",
    "DEFAULT_RRF_WEIGHTS",
    "FieldDefinition",
    "FieldRegistry",
    "FieldType",
    "QuerySyntaxError",
    "RankedCandidate",
    "RankingBranch",
    "SearchBackend",
    "SearchEmbeddingJob",
    "SearchEmbeddingModel",
    "SearchEmbeddingQueue",
    "SearchEmbeddingService",
    "SearchFieldRegistryProvider",
    "SearchQueryParser",
    "SearchService",
    "SearchSlice",
    "is_filter_only",
    "positive_text_expressions",
    "reciprocal_rank_fusion",
]
