"""Align build search documents with the review vocabulary."""

from alembic import op

revision: str = "5fa81c27d6b4"
down_revision: str | None = "d6b8f4310ae2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    _rename_review_statuses(("confirmed", "approved"), ("denied", "rejected"))


def downgrade() -> None:
    _rename_review_statuses(("approved", "confirmed"), ("rejected", "denied"))


def _rename_review_statuses(approval: tuple[str, str], rejection: tuple[str, str]) -> None:
    for previous, current in (approval, rejection):
        op.execute(
            f"""
            UPDATE search_document_facets AS facet
            SET text_value = '{current}'
            FROM search_documents AS document
            WHERE document.id = facet.document_id
              AND document.resource_kind = 'build'
              AND facet.field_name = 'status'
              AND facet.text_value = '{previous}'
            """
        )
        op.execute(
            f"""
            UPDATE search_documents
            SET status = '{current}'
            WHERE resource_kind = 'build' AND status = '{previous}'
            """
        )

    # Rejected builds can carry the old review wording in their indexed titles.
    op.execute(
        f"""
        INSERT INTO search_projection_queue (resource_kind, source_key)
        SELECT 'build', source_key
        FROM search_documents
        WHERE resource_kind = 'build' AND status = '{rejection[1]}'
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )
