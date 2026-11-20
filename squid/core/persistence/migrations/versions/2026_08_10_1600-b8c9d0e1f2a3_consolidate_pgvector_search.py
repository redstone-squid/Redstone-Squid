"""Consolidate embeddings in PostgreSQL.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-10 16:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add pgvector indexes and dead-letter state for embedding work."""
    op.alter_column(
        "builds",
        "embedding",
        existing_type=VECTOR(1536),
        comment="Application-owned semantic vector stored in the authoritative build row.",
        existing_comment='This is not actually being used. See "vecs"."builds" instead',
    )
    op.add_column("search_embedding_queue", sa.Column("dead_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.drop_index("search_embedding_queue_ready_idx", table_name="search_embedding_queue")
    op.create_index(
        "search_embedding_queue_ready_idx",
        "search_embedding_queue",
        ["enqueued_at"],
        postgresql_where=sa.text("locked_at IS NULL AND dead_at IS NULL"),
    )
    op.create_index(
        "builds_embedding_hnsw_idx",
        "builds",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("embedding IS NOT NULL"),
    )
    op.create_index(
        "search_documents_embedding_hnsw_idx",
        "search_documents",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("embedding IS NOT NULL"),
    )


def downgrade() -> None:
    """Restore the former queue predicate and remove approximate indexes."""
    op.drop_index("search_documents_embedding_hnsw_idx", table_name="search_documents")
    op.drop_index("builds_embedding_hnsw_idx", table_name="builds")
    op.drop_index("search_embedding_queue_ready_idx", table_name="search_embedding_queue")
    op.create_index(
        "search_embedding_queue_ready_idx",
        "search_embedding_queue",
        ["enqueued_at"],
        postgresql_where=sa.text("locked_at IS NULL"),
    )
    op.drop_column("search_embedding_queue", "dead_at")
    op.alter_column(
        "builds",
        "embedding",
        existing_type=VECTOR(1536),
        comment='This is not actually being used. See "vecs"."builds" instead',
        existing_comment="Application-owned semantic vector stored in the authoritative build row.",
    )
