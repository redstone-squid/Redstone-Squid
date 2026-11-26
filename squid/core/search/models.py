"""SQLAlchemy models for indexed search projections."""

import uuid
from decimal import Decimal

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.config import EMBEDDING_DIMENSION
from squid.core.persistence.base import Base
from squid.core.persistence.queue_models import DeadLetterColumns, QueueColumns
from squid.core.persistence.types import InstantUTC


class SearchDocument(Base, kw_only=True):
    """A searchable resource: what one search result shows, and the vector that finds it.

    Display columns and `document_data` render the hit. Every searchable fact is
    a SearchValue row; `search_vector` and `heading_terms` are derived from those
    rows when the document is written, for fast recall and ranking.
    """

    __tablename__ = "search_documents"
    __table_args__ = (
        UniqueConstraint("resource_kind", "source_key", name="search_documents_resource_key"),
        Index("search_documents_search_vector_idx", "search_vector", postgresql_using="gin"),
        Index(
            "search_documents_title_trgm_idx",
            "normalized_title",
            postgresql_using="gin",
            postgresql_ops={"normalized_title": "gin_trgm_ops"},
        ),
        Index("search_documents_scope_idx", "resource_kind", "status"),
        Index(
            "search_documents_embedding_hnsw_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("embedding IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    resource_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str | None] = mapped_column(Text, default=None)
    normalized_title: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Normalized title and subtitle, for title order and suggestions."
    )
    heading_terms: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
        default_factory=list,
        comment="Distinct sorted terms of the title and subtitle, to recognize a query that names the heading.",
    )
    search_vector: Mapped[str] = mapped_column(TSVECTOR, nullable=False)
    document_data: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    source_hash: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Digest of the whole document, to skip rewriting it unchanged."
    )
    embedding_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"), default="")
    embedding_hash: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Digest of embedding_text; the embedding is kept while it matches."
    )
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(EMBEDDING_DIMENSION), default=None)
    embedding_model: Mapped[str | None] = mapped_column(Text, default=None)
    refreshed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class SearchValue(Base, kw_only=True):
    """One typed value of a document's field, such as one of a build's creators.

    Text values keep their original text for display and highlighting, a
    normalized form for keyword comparisons, and a vector of their terms for
    word and phrase matches scoped to the field. Record holder values carry the
    holder's build ID, so a clause can be matched against a single holder.
    """

    __tablename__ = "search_values"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(text_value, numeric_value, timestamp_value, boolean_value) = 1",
            name="search_values_one_value_check",
        ),
        CheckConstraint(
            "(text_value IS NULL) = (normalized_text IS NULL) AND (text_value IS NULL) = (term_vector IS NULL)",
            name="search_values_text_forms_check",
        ),
        UniqueConstraint("document_id", "field_name", "ordinal", name="search_values_identity_key"),
        Index(
            "search_values_holder_idx",
            "document_id",
            "holder_build_id",
            "field_name",
            postgresql_where=text("holder_build_id IS NOT NULL"),
        ),
        Index(
            "search_values_keyword_idx",
            "field_name",
            "normalized_text",
            postgresql_where=text("normalized_text IS NOT NULL"),
        ),
        Index(
            # Prefix completion uses lower(text_value) LIKE 'prefix%'. text_pattern_ops
            # lets PostgreSQL use this index when the collation's ordering differs
            # from the byte ordering needed for prefix matches.
            # Keep the operator class inside the SQL expression: postgresql_ops keys
            # do not identify this unlabeled expression. Alembic skips comparison of
            # this form, so changes must also be made explicitly in a migration.
            "search_values_text_prefix_idx",
            "field_name",
            text("lower(text_value) text_pattern_ops"),
            postgresql_where=text("text_value IS NOT NULL"),
        ),
        Index("search_values_term_vector_idx", "term_vector", postgresql_using="gin"),
        Index(
            "search_values_numeric_idx",
            "field_name",
            "numeric_value",
            postgresql_where=text("numeric_value IS NOT NULL"),
        ),
        Index(
            "search_values_timestamp_idx",
            "field_name",
            "timestamp_value",
            postgresql_where=text("timestamp_value IS NOT NULL"),
        ),
        Index(
            "search_values_boolean_idx",
            "field_name",
            "boolean_value",
            postgresql_where=text("boolean_value IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("search_documents.id", name="search_values_document_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    holder_build_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    text_value: Mapped[str | None] = mapped_column(Text, default=None)
    normalized_text: Mapped[str | None] = mapped_column(Text, default=None)
    term_vector: Mapped[str | None] = mapped_column(TSVECTOR, default=None)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric, default=None)
    timestamp_value: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, default=None)


class SearchTerm(Base, kw_only=True):
    """A term that some document has contained, so queries can find close spellings of a word.

    Rows are only added. A term no document still contains matches nothing, so it costs
    nothing but a row.
    """

    __tablename__ = "search_terms"

    term: Mapped[str] = mapped_column(Text, primary_key=True)


class SearchProjectionQueueItem(QueueColumns, DeadLetterColumns, Base, kw_only=True):
    """A durable request to refresh or delete a projected search resource."""

    __tablename__ = "search_projection_queue"
    __table_args__ = (
        CheckConstraint(
            "action IN ('upsert', 'delete')",
            name="search_projection_queue_action_check",
        ),
        UniqueConstraint("resource_kind", "source_key", name="search_projection_queue_resource_key"),
        Index(
            "search_projection_queue_ready_idx",
            "available_at",
            postgresql_where=text("claimed_at IS NULL AND dead_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    resource_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'upsert'"))
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )

    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(),
        nullable=False,
        server_default=func.now(),
        default_factory=Instant.now,
        comment="Earliest time a worker may claim this item; retries move it into the future.",
    )

    claim_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        default=None,
        comment="Token generated for the current claim; stale workers cannot acknowledge a newer claim.",
    )


class SearchEmbeddingQueueItem(QueueColumns, DeadLetterColumns, Base, kw_only=True):
    """A durable request to embed a search document whose embedding text changed."""

    __tablename__ = "search_embedding_queue"
    __table_args__ = (
        Index(
            "search_embedding_queue_ready_idx",
            "available_at",
            postgresql_where=text("claimed_at IS NULL AND dead_at IS NULL"),
        ),
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

    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(),
        nullable=False,
        server_default=func.now(),
        default_factory=Instant.now,
        comment="Earliest time a worker may claim this item; retries move it into the future.",
    )

    claim_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        default=None,
        comment="Token generated for the current claim; stale workers cannot acknowledge a newer claim.",
    )
