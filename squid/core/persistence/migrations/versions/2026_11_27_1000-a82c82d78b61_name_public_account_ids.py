"""Name an account's public UUID public_account_id, and its stored copies account_id.

The column was public_creator_id, from when the public ID only addressed the
creator page. "Creator" now means a credited name, so the column, the merge
redirect table, and the keys that notification payloads and search documents
store the ID under follow the account naming the API uses.

Revision ID: a82c82d78b61
Revises: 5d2b8e4f1a63
Create Date: 2026-11-27 10:00:00+00:00
"""

from alembic import op

revision: str = "a82c82d78b61"
down_revision: str | None = "5d2b8e4f1a63"
branch_labels: str | None = None
depends_on: str | None = None


def _rename_json_key(table: str, column: str, old: str, new: str) -> None:
    op.execute(
        f"UPDATE {table} SET {column} = {column} - '{old}' || jsonb_build_object('{new}', {column} -> '{old}') "
        f"WHERE {column} ? '{old}'"
    )


def upgrade() -> None:
    op.execute("ALTER TABLE accounts RENAME COLUMN public_creator_id TO public_account_id")
    op.execute(
        "ALTER TABLE accounts RENAME CONSTRAINT accounts_public_creator_id_key TO accounts_public_account_id_key"
    )
    op.execute("ALTER TABLE public_creator_redirects RENAME TO public_account_redirects")
    op.execute(
        "ALTER TABLE public_account_redirects RENAME COLUMN retired_public_creator_id TO retired_public_account_id"
    )
    op.execute(
        "ALTER TABLE public_account_redirects RENAME CONSTRAINT public_creator_redirects_pkey "
        "TO public_account_redirects_pkey"
    )
    op.execute(
        "ALTER TABLE public_account_redirects RENAME CONSTRAINT public_creator_redirects_target_account_id_fkey "
        "TO public_account_redirects_target_account_id_fkey"
    )
    op.execute("ALTER INDEX public_creator_redirects_target_idx RENAME TO public_account_redirects_target_idx")
    _rename_json_key("notifications", "payload", "creator_ids", "account_ids")
    _rename_json_key("search_documents", "document_data", "creator_ids", "account_ids")
    op.execute("UPDATE search_values SET field_name = 'account_id' WHERE field_name = 'creator_id'")


def downgrade() -> None:
    op.execute("UPDATE search_values SET field_name = 'creator_id' WHERE field_name = 'account_id'")
    _rename_json_key("search_documents", "document_data", "account_ids", "creator_ids")
    _rename_json_key("notifications", "payload", "account_ids", "creator_ids")
    op.execute("ALTER INDEX public_account_redirects_target_idx RENAME TO public_creator_redirects_target_idx")
    op.execute(
        "ALTER TABLE public_account_redirects RENAME CONSTRAINT public_account_redirects_target_account_id_fkey "
        "TO public_creator_redirects_target_account_id_fkey"
    )
    op.execute(
        "ALTER TABLE public_account_redirects RENAME CONSTRAINT public_account_redirects_pkey "
        "TO public_creator_redirects_pkey"
    )
    op.execute(
        "ALTER TABLE public_account_redirects RENAME COLUMN retired_public_account_id TO retired_public_creator_id"
    )
    op.execute("ALTER TABLE public_account_redirects RENAME TO public_creator_redirects")
    op.execute(
        "ALTER TABLE accounts RENAME CONSTRAINT accounts_public_account_id_key TO accounts_public_creator_id_key"
    )
    op.execute("ALTER TABLE accounts RENAME COLUMN public_account_id TO public_creator_id")
