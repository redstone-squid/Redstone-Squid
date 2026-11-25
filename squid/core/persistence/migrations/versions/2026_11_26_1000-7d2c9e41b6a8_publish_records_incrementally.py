"""Publish records per record, recomputing only what a build change affects.

A result now belongs to its record rather than to a kind-wide run: each record
has at most one current result, updated in place. Records whose builds cannot
compete lose their result instead of storing a no-candidate status. Runs become
a log of published batches, holder history is kept per record, and a membership
index lets a build change find the records it used to belong to.

Existing current results and history are kept. Superseded runs' results and
no-candidate results are deleted. The queue is reset to one full recompute per
kind, which fills the membership index and fingerprints.

Revision ID: 7d2c9e41b6a8
Revises: 4e1b7c9d2a63
Create Date: 2026-11-26 10:00:00+00:00
"""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7d2c9e41b6a8"
down_revision: str | None = "4e1b7c9d2a63"
branch_labels: str | None = None
depends_on: str | None = None

_BUILD_CHANGE = "CREATE FUNCTION public.enqueue_build_change() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_build_id bigint;\n    target_kind text;\n    previous_kind text;\n    build_exists boolean := true;\n    build_deleted boolean := TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE';\nBEGIN\n    IF TG_TABLE_NAME = 'builds' THEN\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;\n        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);\n        IF TG_OP = 'UPDATE' THEN\n            previous_kind := lower(OLD.category);\n        END IF;\n    ELSE\n        -- When the row is going away with its build, the build's own trigger queues the deletion.\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;\n        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;\n        build_exists := FOUND;\n    END IF;\n\n    -- A link change queues only the Discord sync.\n    IF TG_TABLE_NAME <> 'build_links' THEN\n        PERFORM public.upsert_search_projection_request(\n            'build', target_build_id::text, CASE WHEN build_deleted THEN 'delete' ELSE 'upsert' END\n        );\n\n        IF target_kind IN ('door', 'extender') OR previous_kind IN ('door', 'extender') THEN\n            -- One item per build and kind; a build moving between kinds affects both. Deleted builds\n            -- keep their ID, so the worker can still find the records they leave. As in the\n            -- upsert_*_request functions, clearing the claim token keeps an older worker from\n            -- acknowledging the new request.\n            INSERT INTO public.record_recompute_queue\n                (scope_key, build_kind, target, build_id, reasons, enqueued_at, available_at)\n            SELECT kind || ':build:' || target_build_id, kind, 'build', target_build_id,\n                '[\"source_change\"]'::jsonb, now(), now()\n            FROM (\n                SELECT DISTINCT kind FROM unnest(ARRAY[target_kind, previous_kind]) AS kinds(kind)\n                WHERE kind IN ('door', 'extender')\n            ) AS affected\n            ORDER BY kind\n            ON CONFLICT (scope_key) DO UPDATE\n            SET enqueued_at = EXCLUDED.enqueued_at,\n                available_at = EXCLUDED.available_at,\n                attempts = 0,\n                claimed_at = NULL,\n                claim_token = NULL,\n                last_error = NULL;\n        END IF;\n    END IF;\n\n    IF build_deleted THEN\n        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'delete');\n    ELSIF build_exists THEN\n        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'refresh');\n\n        -- A review card embeds the build it is voting on, so a build change makes the\n        -- session's cards stale too. Their posts are keyed by session, not by build, so\n        -- the sessions have to be enqueued in their own right.\n        PERFORM public.upsert_discord_sync_request('vote_session', bvs.vote_session_id::text, 'refresh')\n        FROM public.build_vote_sessions bvs\n        WHERE bvs.build_id = target_build_id;\n    END IF;\n    RETURN NULL;\nEND;\n$$;"
_SEARCH = "CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nBEGIN\n    IF TG_TABLE_NAME = 'title_results' THEN\n        PERFORM public.upsert_search_projection_request(\n            'record',\n            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END)::text,\n            CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END\n        );\n    ELSE\n        PERFORM public.upsert_search_projection_request(\n            'record',\n            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END)::text,\n            'upsert'\n        );\n    END IF;\n    RETURN NULL;\nEND;\n$$;"
_OLD_BUILD_CHANGE = "CREATE FUNCTION public.enqueue_build_change() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_build_id bigint;\n    target_kind text;\n    previous_kind text;\n    build_exists boolean := true;\n    build_deleted boolean := TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE';\nBEGIN\n    IF TG_TABLE_NAME = 'builds' THEN\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;\n        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);\n        IF TG_OP = 'UPDATE' THEN\n            previous_kind := lower(OLD.category);\n        END IF;\n    ELSE\n        -- When the row is going away with its build, the build's own trigger queues the deletion.\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;\n        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;\n        build_exists := FOUND;\n    END IF;\n\n    -- A link change queues only the Discord sync.\n    IF TG_TABLE_NAME <> 'build_links' THEN\n        PERFORM public.upsert_search_projection_request(\n            'build', target_build_id::text, CASE WHEN build_deleted THEN 'delete' ELSE 'upsert' END\n        );\n\n        IF target_kind IN ('door', 'extender') OR previous_kind IN ('door', 'extender') THEN\n            -- As in the upsert_*_request functions, clearing the claim token keeps an older worker\n            -- from acknowledging the new request.\n            INSERT INTO public.record_recompute_queue\n                (scope_key, build_kind, build_id, reasons, enqueued_at, available_at)\n            SELECT kind, kind,\n                CASE WHEN build_deleted THEN NULL ELSE target_build_id END,\n                '[\"source_change\"]'::jsonb, now(), now()\n            FROM (\n                SELECT DISTINCT kind FROM unnest(ARRAY[target_kind, previous_kind]) AS kinds(kind)\n                WHERE kind IN ('door', 'extender')\n            ) AS affected\n            ORDER BY kind\n            ON CONFLICT (scope_key) DO UPDATE\n            SET build_id = EXCLUDED.build_id,\n                reasons = (\n                    SELECT jsonb_agg(DISTINCT reason)\n                    FROM jsonb_array_elements_text(\n                        record_recompute_queue.reasons || EXCLUDED.reasons\n                    ) AS reason\n                ),\n                enqueued_at = EXCLUDED.enqueued_at,\n                available_at = EXCLUDED.available_at,\n                attempts = 0,\n                claimed_at = NULL,\n                claim_token = NULL,\n                last_error = NULL;\n        END IF;\n    END IF;\n\n    IF build_deleted THEN\n        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'delete');\n    ELSIF build_exists THEN\n        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'refresh');\n\n        -- A review card embeds the build it is voting on, so a build change makes the\n        -- session's cards stale too. Their posts are keyed by session, not by build, so\n        -- the sessions have to be enqueued in their own right.\n        PERFORM public.upsert_discord_sync_request('vote_session', bvs.vote_session_id::text, 'refresh')\n        FROM public.build_vote_sessions bvs\n        WHERE bvs.build_id = target_build_id;\n    END IF;\n    RETURN NULL;\nEND;\n$$;"
_OLD_SEARCH = "CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nBEGIN\n    IF TG_TABLE_NAME = 'title_results' THEN\n        PERFORM public.upsert_search_projection_request(\n            'record',\n            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END)::text,\n            CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END\n        );\n    ELSIF TG_TABLE_NAME = 'title_holders' THEN\n        PERFORM public.upsert_search_projection_request(\n            'record',\n            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END)::text,\n            'upsert'\n        );\n    ELSE\n        PERFORM public.upsert_search_projection_request('record', 'result:' || rr.id::text, 'upsert')\n        FROM public.title_results rr\n        WHERE rr.run_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;\n    END IF;\n    RETURN NULL;\nEND;\n$$;"
_RUN_SEARCH_TRIGGER = "CREATE TRIGGER record_computation_runs_enqueue_search\n    AFTER INSERT OR DELETE OR UPDATE OF is_active ON public.record_computation_runs\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_computed_record_search_projection();"


def _drop_json_status(column: str, table: str, path: str) -> str:
    return f"UPDATE public.{table} SET {column} = {column} #- '{path}' WHERE {column} #> '{path}' IS NOT NULL"


def upgrade() -> None:
    op.drop_entity(PGTrigger.from_sql(_RUN_SEARCH_TRIGGER))
    op.replace_entity(PGFunction.from_sql(_SEARCH))
    op.replace_entity(PGFunction.from_sql(_BUILD_CHANGE))

    # Deliveries of the replaced event have no handler any more.
    op.execute(
        """
        DELETE FROM public.domain_event_deliveries d
        USING public.domain_events e
        WHERE d.event_id = e.id AND e.event_type = 'record_run.activated'
        """
    )

    # Keep only current results that have holders, and the history of current runs.
    op.execute(
        """
        DELETE FROM public.title_results r
        USING public.record_computation_runs run
        WHERE r.run_id = run.id AND (NOT run.is_active OR r.status = 'no_candidate')
        """
    )
    op.execute(
        """
        DELETE FROM public.title_holder_history h
        USING public.record_computation_runs run
        WHERE h.run_id = run.id AND NOT run.is_active
        """
    )

    op.add_column(
        "record_titles",
        sa.Column(
            "input_generation",
            sa.BigInteger(),
            nullable=True,
            comment="The kind's input generation last published or retired here; older batches leave the record alone.",
        ),
    )
    op.add_column(
        "record_titles",
        sa.Column(
            "retired_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the record last lost its competitors; null while it has a result.",
        ),
    )

    op.add_column("title_results", sa.Column("title_id", sa.UUID(), nullable=True))
    op.add_column(
        "title_results",
        sa.Column(
            "fingerprint",
            sa.Text(),
            nullable=True,
            comment="A digest of the published contents, to skip republishing unchanged results.",
        ),
    )
    op.add_column(
        "title_results",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
    op.execute(
        """
        UPDATE public.title_results r
        SET title_id = d.title_id,
            fingerprint = '',
            last_checked_at = coalesce(run.last_checked_at, r.computed_at)
        FROM public.title_definitions d, public.record_computation_runs run
        WHERE d.id = r.definition_id AND run.id = r.run_id
        """
    )
    op.alter_column("title_results", "title_id", nullable=False)
    op.alter_column("title_results", "fingerprint", nullable=False)
    op.alter_column("title_results", "last_checked_at", nullable=False)
    op.drop_constraint("title_results_status_check", "title_results", type_="check")
    op.drop_column("title_results", "status")
    op.drop_constraint("title_results_run_definition_key", "title_results", type_="unique")
    op.create_unique_constraint("title_results_title_key", "title_results", ["title_id"])
    op.create_foreign_key(
        "title_results_title_id_fkey", "title_results", "record_titles", ["title_id"], ["public_id"], ondelete="CASCADE"
    )
    op.drop_constraint("title_results_run_id_fkey", "title_results", type_="foreignkey")
    op.create_foreign_key(
        "title_results_run_id_fkey",
        "title_results",
        "record_computation_runs",
        ["run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("title_results_run_idx", "title_results", ["run_id"])
    op.alter_column("title_results", "run_id", comment="The batch that last changed this result.")
    op.execute(_drop_json_status("door_evidence", "title_results", "{evaluation,resolution,status}"))
    op.execute(
        """
        UPDATE public.door_range_runs run
        SET result = jsonb_set(
            run.result,
            '{analysis,regions}',
            (
                SELECT jsonb_agg(region #- '{resolution,status}' ORDER BY position)
                FROM jsonb_array_elements(run.result #> '{analysis,regions}') WITH ORDINALITY AS item(region, position)
            )
        )
        WHERE jsonb_typeof(run.result #> '{analysis,regions}') = 'array'
          AND jsonb_array_length(run.result #> '{analysis,regions}') > 0
        """
    )

    op.add_column("title_holder_history", sa.Column("title_id", sa.UUID(), nullable=True))
    op.execute(
        """
        UPDATE public.title_holder_history h SET title_id = d.title_id
        FROM public.title_definitions d WHERE d.id = h.definition_id
        """
    )
    op.alter_column("title_holder_history", "title_id", nullable=False)
    op.drop_constraint("title_holder_history_identity_key", "title_holder_history", type_="unique")
    op.drop_index("title_holder_history_definition_idx", table_name="title_holder_history")
    op.drop_constraint("title_holder_history_run_id_fkey", "title_holder_history", type_="foreignkey")
    op.drop_column("title_holder_history", "run_id")
    op.create_unique_constraint(
        "title_holder_history_identity_key", "title_holder_history", ["title_id", "build_id", "held_from"]
    )
    op.create_index("title_holder_history_definition_idx", "title_holder_history", ["definition_id"])
    op.create_foreign_key(
        "title_holder_history_title_id_fkey",
        "title_holder_history",
        "record_titles",
        ["title_id"],
        ["public_id"],
        ondelete="CASCADE",
    )

    op.drop_index("record_computation_runs_one_active_idx", table_name="record_computation_runs")
    op.drop_constraint("record_computation_runs_status_check", "record_computation_runs", type_="check")
    op.drop_constraint("record_computation_runs_version_id_fkey", "record_computation_runs", type_="foreignkey")
    for column in (
        "version_id",
        "status",
        "is_active",
        "error",
        "checked_generation",
        "publication_fingerprint",
        "last_checked_at",
    ):
        op.drop_column("record_computation_runs", column)
    op.add_column(
        "record_computation_runs", sa.Column("full", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )
    op.add_column(
        "record_computation_runs",
        sa.Column("input_generation", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.alter_column("record_computation_runs", "input_generation", server_default=None)
    op.execute("UPDATE public.record_computation_runs SET completed_at = started_at WHERE completed_at IS NULL")
    op.alter_column("record_computation_runs", "completed_at", nullable=False, server_default=sa.text("now()"))

    op.create_table(
        "record_members",
        sa.Column("title_id", sa.UUID(), nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["title_id"], ["record_titles.public_id"], name="record_members_title_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("title_id", "build_id"),
    )
    op.create_index("record_members_build_idx", "record_members", ["build_id"])
    op.create_table(
        "record_drift",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("title_id", sa.UUID(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "published",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Holders, gaps and members before the backstop; empty for a retired record.",
        ),
        sa.Column(
            "computed",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="The same facts as the backstop computed them; empty when it retired the record.",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["record_computation_runs.id"], name="record_drift_run_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["title_id"], ["record_titles.public_id"], name="record_drift_title_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="A record a backstop recompute changed although no queued change explained it.",
    )
    op.create_index("record_drift_title_idx", "record_drift", ["title_id"])
    op.create_index("record_drift_run_id_idx", "record_drift", ["run_id"])

    op.execute("DELETE FROM public.record_recompute_queue")
    op.drop_constraint("record_recompute_queue_build_id_fkey", "record_recompute_queue", type_="foreignkey")
    op.add_column(
        "record_recompute_queue", sa.Column("target", sa.Text(), server_default=sa.text("'kind'"), nullable=False)
    )
    op.add_column("record_recompute_queue", sa.Column("title_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "record_recompute_queue_title_id_fkey",
        "record_recompute_queue",
        "record_titles",
        ["title_id"],
        ["public_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "record_recompute_queue_target_check",
        "record_recompute_queue",
        "target IN ('kind', 'backstop', 'build', 'record') "
        "AND (build_id IS NOT NULL) = (target = 'build') AND (title_id IS NOT NULL) = (target = 'record')",
    )
    op.create_index("record_recompute_queue_title_idx", "record_recompute_queue", ["title_id"])
    op.create_index("record_recompute_queue_kind_idx", "record_recompute_queue", ["build_kind"])
    op.execute(
        """
        INSERT INTO public.record_recompute_queue (scope_key, build_kind, target, reasons)
        VALUES ('door', 'door', 'kind', '["incremental_records"]'::jsonb),
               ('extender', 'extender', 'kind', '["incremental_records"]'::jsonb)
        """
    )

    op.drop_constraint("notifications_kind_check", "notifications", type_="check")
    op.create_check_constraint(
        "notifications_kind_check",
        "notifications",
        "kind IN ('build_confirmed', 'build_denied', 'creator_build_confirmed', "
        "'record_gained', 'record_retired', 'staff_build_submitted')",
    )

    # Table comments follow the model docstrings.
    op.create_table_comment(
        "record_computation_runs",
        "One published batch of one build kind; results and drift name the run that wrote them.",
    )
    op.create_table_comment(
        "record_members",
        "A build in a live record's category, whether or not it competes.\n\nThere is no foreign key to builds: a deleted build's rows must survive until\na worker recomputes the records it leaves.",
    )
    op.create_table_comment(
        "record_recompute_queue",
        "A durable request to recompute what one change affects.\n\n`scope_key` deduplicates requests: the kind alone for a whole-kind\nrecompute, otherwise the kind and target, such as `door:build:12`.",
    )
    op.create_table_comment(
        "title_holder_history",
        "A reconstructed interval in a record's beaten-record chronology.\n\nEach publication replaces a record's periods. Retirement keeps them and\ncloses the open one.",
    )
    op.create_table_comment("title_results", "A live record's current outcome; a record without competitors has none.")


def downgrade() -> None:
    # Restores the shape only: current results return as one active run per kind and version.
    op.create_table_comment(
        "record_computation_runs",
        "A computation attempt for one build kind and version, including its publication state.",
    )
    op.create_table_comment("record_recompute_queue", "A durable request to recompute an affected record scope.")
    op.create_table_comment(
        "title_holder_history", "A reconstructed interval in a definition's beaten-record chronology."
    )
    op.create_table_comment("title_results", "The outcome for one definition in a computation run.")
    op.execute("DELETE FROM public.notifications WHERE kind = 'record_retired'")
    op.drop_constraint("notifications_kind_check", "notifications", type_="check")
    op.create_check_constraint(
        "notifications_kind_check",
        "notifications",
        "kind IN ('build_confirmed', 'build_denied', 'creator_build_confirmed', "
        "'record_gained', 'staff_build_submitted')",
    )

    op.execute("DELETE FROM public.record_recompute_queue WHERE target <> 'kind'")
    op.drop_index("record_recompute_queue_kind_idx", table_name="record_recompute_queue")
    op.drop_index("record_recompute_queue_title_idx", table_name="record_recompute_queue")
    op.drop_constraint("record_recompute_queue_target_check", "record_recompute_queue", type_="check")
    op.drop_constraint("record_recompute_queue_title_id_fkey", "record_recompute_queue", type_="foreignkey")
    op.drop_column("record_recompute_queue", "title_id")
    op.drop_column("record_recompute_queue", "target")
    op.create_foreign_key(
        "record_recompute_queue_build_id_fkey",
        "record_recompute_queue",
        "builds",
        ["build_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_index("record_drift_run_id_idx", table_name="record_drift")
    op.drop_index("record_drift_title_idx", table_name="record_drift")
    op.drop_table("record_drift")
    op.drop_index("record_members_build_idx", table_name="record_members")
    op.drop_table("record_members")

    op.alter_column("record_computation_runs", "completed_at", nullable=True, server_default=None)
    op.drop_column("record_computation_runs", "input_generation")
    op.drop_column("record_computation_runs", "full")
    op.add_column("record_computation_runs", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("record_computation_runs", sa.Column("publication_fingerprint", sa.Text(), nullable=True))
    op.add_column("record_computation_runs", sa.Column("checked_generation", sa.BigInteger(), nullable=True))
    op.add_column("record_computation_runs", sa.Column("error", sa.Text(), nullable=True))
    op.add_column(
        "record_computation_runs", sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )
    op.add_column(
        "record_computation_runs", sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False)
    )
    op.add_column("record_computation_runs", sa.Column("version_id", sa.SmallInteger(), nullable=True))
    op.create_foreign_key(
        "record_computation_runs_version_id_fkey",
        "record_computation_runs",
        "versions",
        ["version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "record_computation_runs_status_check",
        "record_computation_runs",
        "status IN ('running', 'completed', 'failed')",
    )
    op.create_index(
        "record_computation_runs_one_active_idx",
        "record_computation_runs",
        ["build_kind", "version_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        postgresql_nulls_not_distinct=True,
    )

    op.drop_constraint("title_holder_history_title_id_fkey", "title_holder_history", type_="foreignkey")
    op.drop_index("title_holder_history_definition_idx", table_name="title_holder_history")
    op.drop_constraint("title_holder_history_identity_key", "title_holder_history", type_="unique")
    op.execute("DELETE FROM public.title_holder_history")
    op.add_column("title_holder_history", sa.Column("run_id", sa.BigInteger(), nullable=False))
    op.drop_column("title_holder_history", "title_id")
    op.create_foreign_key(
        "title_holder_history_run_id_fkey",
        "title_holder_history",
        "record_computation_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "title_holder_history_identity_key",
        "title_holder_history",
        ["run_id", "definition_id", "build_id", "held_from"],
    )
    op.create_index("title_holder_history_definition_idx", "title_holder_history", ["definition_id", "held_from"])

    op.execute("DELETE FROM public.title_results")
    op.alter_column("title_results", "run_id", comment=None)
    op.drop_index("title_results_run_idx", table_name="title_results")
    op.drop_constraint("title_results_run_id_fkey", "title_results", type_="foreignkey")
    op.create_foreign_key(
        "title_results_run_id_fkey",
        "title_results",
        "record_computation_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("title_results_title_id_fkey", "title_results", type_="foreignkey")
    op.drop_constraint("title_results_title_key", "title_results", type_="unique")
    op.create_unique_constraint("title_results_run_definition_key", "title_results", ["run_id", "definition_id"])
    op.add_column("title_results", sa.Column("status", sa.Text(), nullable=False))
    op.create_check_constraint("title_results_status_check", "title_results", "status IN ('resolved', 'no_candidate')")
    op.drop_column("title_results", "last_checked_at")
    op.drop_column("title_results", "fingerprint")
    op.drop_column("title_results", "title_id")
    op.drop_column("record_titles", "retired_at")
    op.drop_column("record_titles", "input_generation")
    op.execute(
        "INSERT INTO public.record_recompute_queue (scope_key, build_kind, reasons) VALUES ('door', 'door', '[]'), ('extender', 'extender', '[]') ON CONFLICT DO NOTHING"
    )

    op.replace_entity(PGFunction.from_sql(_OLD_BUILD_CHANGE))
    op.replace_entity(PGFunction.from_sql(_OLD_SEARCH))
    op.create_entity(PGTrigger.from_sql(_RUN_SEARCH_TRIGGER))
