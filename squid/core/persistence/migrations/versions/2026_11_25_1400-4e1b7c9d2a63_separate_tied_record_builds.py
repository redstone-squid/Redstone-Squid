"""Store builds tied with record holders and stop publishing unresolved records.

Missing tie-breakers now make a build lose with a recorded gap instead of leaving
the record without a holder. Stored unresolved results had no holders, so they
become no-candidate results until the queued recomputation replaces them.

Revision ID: 4e1b7c9d2a63
Revises: 39a2de11f648
Create Date: 2026-11-25 14:00:00+00:00
"""

import sqlalchemy as sa

from alembic import op

revision: str = "4e1b7c9d2a63"
down_revision: str | None = "39a2de11f648"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("UPDATE public.title_results SET status = 'no_candidate' WHERE status = 'unresolved'")
    op.execute(
        """
        UPDATE public.title_results
        SET door_evidence = jsonb_set(door_evidence, '{evaluation,resolution,status}', '"no_candidate"')
        WHERE door_evidence #>> '{evaluation,resolution,status}' = 'unresolved'
        """
    )
    op.execute(
        """
        UPDATE public.door_range_runs run
        SET result = jsonb_set(
            run.result,
            '{analysis,regions}',
            (
                SELECT jsonb_agg(
                    CASE
                        WHEN region #>> '{resolution,status}' = 'unresolved'
                        THEN jsonb_set(region, '{resolution,status}', '"no_candidate"')
                        ELSE region
                    END
                    ORDER BY position
                )
                FROM jsonb_array_elements(run.result #> '{analysis,regions}') WITH ORDINALITY AS item(region, position)
            )
        )
        WHERE jsonb_typeof(run.result #> '{analysis,regions}') = 'array'
          AND jsonb_array_length(run.result #> '{analysis,regions}') > 0
        """
    )
    op.drop_constraint("title_results_status_check", "title_results", type_="check")
    op.create_check_constraint("title_results_status_check", "title_results", "status IN ('resolved', 'no_candidate')")
    op.drop_index("title_results_provisional_build_idx", table_name="title_results")
    op.drop_column("title_results", "provisional_build_id")

    op.create_table(
        "title_tied_builds",
        sa.Column("result_id", sa.BigInteger(), nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["build_id"], ["builds.id"], name="title_tied_builds_build_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["result_id"], ["title_results.id"], name="title_tied_builds_result_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("result_id", "build_id"),
        comment="A build that matches a record's holders on every metric but was completed later.",
    )
    op.create_index("title_tied_builds_build_idx", "title_tied_builds", ["build_id"])

    op.execute(
        """
        INSERT INTO public.record_recompute_queue (scope_key, build_kind, reasons)
        SELECT 'tied_records:' || build_kind, build_kind, '["tied_records"]'::jsonb
        FROM public.record_computation_runs
        WHERE is_active AND build_kind IN ('door', 'extender')
        GROUP BY build_kind
        ON CONFLICT (scope_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO public.door_range_recompute_queue (query_id)
        SELECT id FROM public.door_range_registrations
        ON CONFLICT (query_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM public.record_recompute_queue WHERE reasons ? 'tied_records'")
    op.drop_index("title_tied_builds_build_idx", table_name="title_tied_builds")
    op.drop_table("title_tied_builds")
    op.add_column("title_results", sa.Column("provisional_build_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "title_results_provisional_build_id_fkey",
        "title_results",
        "builds",
        ["provisional_build_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("title_results_provisional_build_idx", "title_results", ["provisional_build_id"])
    op.drop_constraint("title_results_status_check", "title_results", type_="check")
    op.create_check_constraint(
        "title_results_status_check", "title_results", "status IN ('resolved', 'unresolved', 'no_candidate')"
    )
