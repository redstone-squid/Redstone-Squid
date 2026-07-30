"""SQLAlchemy models for indexed search projections."""

from __future__ import annotations

from decimal import Decimal

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.config import embedding_dimension_from_environment
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class SearchDocument(Base, kw_only=True):
    """An indexed projection of a searchable application resource."""

    __tablename__ = "search_documents"
    __table_args__ = (
        UniqueConstraint("resource_kind", "source_key", name="search_documents_resource_key"),
        Index("search_documents_title_fts_idx", "title_vector", postgresql_using="gin"),
        Index("search_documents_description_fts_idx", "description_vector", postgresql_using="gin"),
        Index("search_documents_combined_fts_idx", "combined_vector", postgresql_using="gin"),
        Index(
            "search_documents_fuzzy_trgm_idx",
            "fuzzy_text",
            postgresql_using="gin",
            postgresql_ops={"fuzzy_text": "gin_trgm_ops"},
        ),
        Index("search_documents_tags_idx", "tags", postgresql_using="gin"),
        Index("search_documents_scope_idx", "resource_kind", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    resource_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str | None] = mapped_column(Text, default=None)
    normalized_title: Mapped[str] = mapped_column(Text, nullable=False)
    fuzzy_text: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"), default_factory=list
    )
    title_vector: Mapped[str | None] = mapped_column(TSVECTOR, default=None)
    description_vector: Mapped[str | None] = mapped_column(TSVECTOR, default=None)
    combined_vector: Mapped[str | None] = mapped_column(TSVECTOR, default=None)
    document_data: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(embedding_dimension_from_environment()), default=None)
    embedding_model: Mapped[str | None] = mapped_column(Text, default=None)
    refreshed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class SearchDocumentFacet(Base, kw_only=True):
    """A typed, indexed field value belonging to a search document."""

    __tablename__ = "search_document_facets"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(text_value, numeric_value, timestamp_value, boolean_value) = 1",
            name="search_document_facets_one_value_check",
        ),
        UniqueConstraint(
            "document_id",
            "field_name",
            "ordinal",
            name="search_document_facets_identity_key",
        ),
        Index(
            "search_document_facets_text_idx",
            "field_name",
            "text_value",
            postgresql_where=text("text_value IS NOT NULL"),
        ),
        Index(
            "search_document_facets_numeric_idx",
            "field_name",
            "numeric_value",
            postgresql_where=text("numeric_value IS NOT NULL"),
        ),
        Index(
            "search_document_facets_timestamp_idx",
            "field_name",
            "timestamp_value",
            postgresql_where=text("timestamp_value IS NOT NULL"),
        ),
        Index(
            "search_document_facets_boolean_idx",
            "field_name",
            "boolean_value",
            postgresql_where=text("boolean_value IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("search_documents.id", name="search_document_facets_document_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    text_value: Mapped[str | None] = mapped_column(Text, default=None)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric, default=None)
    timestamp_value: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, default=None)


class SearchProjectionQueueItem(Base, kw_only=True):
    """A durable request to refresh or delete a projected search resource."""

    __tablename__ = "search_projection_queue"
    __table_args__ = (
        CheckConstraint(
            "action IN ('upsert', 'delete')",
            name="search_projection_queue_action_check",
        ),
        UniqueConstraint("resource_kind", "source_key", name="search_projection_queue_resource_key"),
        Index("search_projection_queue_ready_idx", "enqueued_at", postgresql_where=text("locked_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    resource_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'upsert'"))
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    locked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


class SearchEmbeddingQueueItem(Base, kw_only=True):
    """A durable request to embed a search document whose source hash changed."""

    __tablename__ = "search_embedding_queue"
    __table_args__ = (
        Index("search_embedding_queue_ready_idx", "enqueued_at", postgresql_where=text("locked_at IS NULL")),
    )

    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("search_documents.id", name="search_embedding_queue_document_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    locked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
