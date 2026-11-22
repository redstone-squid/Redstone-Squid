"""Name record tag links, search filters, and tag change notifications.

Revision ID: f4b7c28a610d
Revises: b9e5d016c4a2
Create Date: 2026-11-22 14:00:00+00:00
"""

from alembic import op

revision: str = "f4b7c28a610d"
down_revision: str | None = "b9e5d016c4a2"
branch_labels: str | None = None
depends_on: str | None = None

_RECORD_TABLE = ("title_definition_facets", "title_definition_tags")
_SEARCH_TABLE = ("search_document_facets", "search_document_filters")
_FUNCTION = ("enqueue_record_taxonomy_change", "enqueue_record_tag_change")
_REASON = ("taxonomy_change", "tag_change")
_RECORD_COLUMNS = (("facet_kind", "tag_role"), ("facet_id", "tag_id"))
_RECORD_COMMENT = (
    "A canonical taxonomy facet belonging to a record definition.",
    "A tag required by a record definition.",
)


def _rename(*, upgrade: bool) -> None:
    source, target = (0, 1) if upgrade else (1, 0)
    for names, constraints, indexes in (
        (
            _RECORD_TABLE,
            (
                ("pkey", "pkey"),
                ("kind_check", "kind_check"),
                ("definition_id_fkey", "definition_id_fkey"),
                ("facet_id_fkey", "tag_id_fkey"),
            ),
            ("lookup_idx",),
        ),
        (
            _SEARCH_TABLE,
            tuple((suffix, suffix) for suffix in ("pkey", "identity_key", "one_value_check", "document_id_fkey")),
            ("boolean_idx", "numeric_idx", "text_idx", "text_prefix_idx", "timestamp_idx"),
        ),
    ):
        old, new = names[source], names[target]
        op.rename_table(old, new, schema="public")
        for suffixes in constraints:
            op.execute(
                f"ALTER TABLE public.{new} RENAME CONSTRAINT {old}_{suffixes[source]} TO {new}_{suffixes[target]}"
            )
        for suffix in indexes:
            op.execute(f"ALTER INDEX public.{old}_{suffix} RENAME TO {new}_{suffix}")

    for names in _RECORD_COLUMNS:
        op.alter_column(_RECORD_TABLE[target], names[source], new_column_name=names[target], schema="public")
    op.execute(f"COMMENT ON TABLE public.{_RECORD_TABLE[target]} IS '{_RECORD_COMMENT[target]}'")
    op.execute(f"ALTER SEQUENCE public.{_SEARCH_TABLE[source]}_id_seq RENAME TO {_SEARCH_TABLE[target]}_id_seq")

    # PostgreSQL tracks the triggers' dependency on the function by OID.
    op.execute(f"ALTER FUNCTION public.{_FUNCTION[source]}() RENAME TO {_FUNCTION[target]}")
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.{_FUNCTION[target]}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO public.record_recompute_queue (scope_key, build_kind, reasons)
            VALUES ('door', 'door', '["{_REASON[target]}"]'::jsonb),
                   ('extender', 'extender', '["{_REASON[target]}"]'::jsonb)
            ON CONFLICT (scope_key) DO UPDATE SET
                enqueued_at = now(), available_at = now(),
                claimed_at = NULL, claim_token = NULL;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        UPDATE public.record_recompute_queue
        SET reasons = replace(reasons::text, '"{_REASON[source]}"', '"{_REASON[target]}"')::jsonb
        WHERE reasons ? '{_REASON[source]}'
        """
    )


def upgrade() -> None:
    _rename(upgrade=True)


def downgrade() -> None:
    _rename(upgrade=False)
