"""Persistence models for cross-resource search."""

"""PostgreSQL search adapters."""

from squid.search.infrastructure.compiler import PostgresSearchQueryCompiler
from squid.search.infrastructure.embeddings import PostgresSearchEmbeddingQueue, PostgresSemanticCandidateProvider
from squid.search.infrastructure.repository import PostgresSearchBackend, SemanticCandidate, SemanticCandidateProvider

__all__ = [
    "PostgresSearchBackend",
    "PostgresSearchEmbeddingQueue",
    "PostgresSearchQueryCompiler",
    "PostgresSemanticCandidateProvider",
    "SemanticCandidate",
    "SemanticCandidateProvider",
]
