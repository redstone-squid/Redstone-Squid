"""Add notification preferences, subscriptions, inbox, and DM outbox.

Revision ID: c5e6f7a8b9c0
Revises: b4d5e6f7a8b9
Create Date: 2026-08-11 11:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy.dialects import postgresql

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "c5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "b4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREVIOUS_EMIT_SQL = """
CREATE OR REPLACE FUNCTION public.emit_domain_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_type text;
    target_kind text;
    target_id bigint;
    target_payload jsonb;
BEGIN
    IF TG_TABLE_NAME = 'builds' THEN
        IF OLD.submission_status IS NOT DISTINCT FROM NEW.submission_status THEN RETURN NULL; END IF;
        target_kind := 'build';
        target_id := NEW.id;
        IF NEW.submission_status = 1 THEN
            target_type := 'build.confirmed';
        ELSIF NEW.submission_status = 2 THEN
            target_type := 'build.denied';
        ELSE
            RETURN NULL;
        END IF;
        target_payload := jsonb_build_object(
            'previous_status', OLD.submission_status,
            'status', NEW.submission_status
        );
    ELSE
        IF OLD.status IS NOT DISTINCT FROM NEW.status OR NEW.status <> 'closed' THEN RETURN NULL; END IF;
        target_kind := 'vote_session';
        target_id := NEW.id;
        target_type := 'vote_session.closed';
        target_payload := jsonb_build_object('kind', NEW.kind, 'result', NEW.result);
    END IF;

    PERFORM public.publish_domain_event(target_type, 1, target_kind, target_id, target_payload);
    RETURN NULL;
END;
$$;
"""


def upgrade() -> None:
    """Create notification persistence and emit richer build lifecycle events."""
    op.add_column(
        "domain_event_deliveries",
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "domain_event_deliveries",
        sa.Column("claim_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    # Old workers could leave timestamp-only leases behind. Releasing those leases is
    # safe under the queue's at-least-once contract and lets the new complete-token
    # invariant be installed without stranding an event.
    op.execute("UPDATE domain_event_deliveries SET claimed_at = NULL WHERE claimed_at IS NOT NULL")
    op.create_check_constraint(
        "domain_event_deliveries_claim_count_nonnegative",
        "domain_event_deliveries",
        "claim_count >= 0",
    )
    op.create_check_constraint(
        "domain_event_deliveries_claim_complete",
        "domain_event_deliveries",
        "(claimed_at IS NULL) = (claim_token IS NULL)",
    )
    op.create_table(
        "notification_profiles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("notice_version", sa.Text(), nullable=True),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("web_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("dm_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("dm_suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(notice_version IS NULL) = (consented_at IS NULL)",
            name="notification_profiles_notice_receipt_complete",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="notification_profiles_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id"),
        comment="A notification-specific notice receipt and independent channel preferences.",
    )
    op.create_table(
        "notification_subscriptions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("filter", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(kind = 'record_filter') = (filter IS NOT NULL)",
            name="notification_subscriptions_filter_complete",
        ),
        sa.CheckConstraint(
            "kind IN ('creator', 'record', 'record_filter')",
            name="notification_subscriptions_kind_check",
        ),
        sa.CheckConstraint(
            "(kind IN ('creator', 'record')) = (subject_id IS NOT NULL)",
            name="notification_subscriptions_subject_complete",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="notification_subscriptions_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="A creator, exact record, or structured record-filter subscription.",
    )
    op.create_index("notification_subscriptions_subject_idx", "notification_subscriptions", ["kind", "subject_id"])
    op.create_index("notification_subscriptions_user_idx", "notification_subscriptions", ["user_id", "created_at"])
    op.create_index(
        "notification_subscriptions_exact_key",
        "notification_subscriptions",
        ["user_id", "kind", "subject_id"],
        unique=True,
        postgresql_where=sa.text("enabled AND subject_id IS NOT NULL"),
    )
    op.create_index(
        "notification_subscriptions_filter_key",
        "notification_subscriptions",
        ["user_id", "kind", "filter"],
        unique=True,
        postgresql_where=sa.text("enabled AND filter IS NOT NULL"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("web_visible", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('build_confirmed', 'build_denied', 'creator_build_confirmed', "
            "'record_gained', 'staff_build_submitted')",
            name="notifications_kind_check",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["domain_events.id"], name="notifications_event_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="notifications_user_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key", name="notifications_source_key_key"),
        comment="An idempotently materialized user notification.",
    )
    op.create_index("notifications_created_idx", "notifications", ["created_at"])
    op.create_index("notifications_user_inbox_idx", "notifications", ["user_id", "id"])
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("notification_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("discord_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "nonce",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="notification_deliveries_attempts_nonnegative"),
        sa.CheckConstraint("generation > 0", name="notification_deliveries_generation_positive"),
        sa.CheckConstraint(
            "(claimed_at IS NULL) = (claim_token IS NULL)",
            name="notification_deliveries_claim_complete",
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            name="notification_deliveries_notification_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="notification_deliveries_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", name="notification_deliveries_notification_id_key"),
        comment="A durable at-least-once Discord DM delivery attempt.",
    )
    op.create_index(
        "notification_deliveries_ready_idx",
        "notification_deliveries",
        ["available_at"],
        postgresql_where=sa.text("claimed_at IS NULL AND dead_at IS NULL AND sent_at IS NULL"),
    )

    for statement in _function("emit_domain_event").to_sql_statement_create_or_replace():
        op.execute(statement)
    op.execute("DROP TRIGGER builds_emit_domain_event ON public.builds")
    op.execute(_trigger("builds_emit_domain_event").to_sql_statement_create())


def downgrade() -> None:
    """Remove notification state and restore update-only build events."""
    op.execute("DROP TRIGGER builds_emit_domain_event ON public.builds")
    op.execute(_PREVIOUS_EMIT_SQL)
    op.execute(
        "CREATE TRIGGER builds_emit_domain_event AFTER UPDATE OF submission_status ON public.builds "
        "FOR EACH ROW EXECUTE FUNCTION public.emit_domain_event()"
    )
    op.drop_index("notification_deliveries_ready_idx", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index("notifications_user_inbox_idx", table_name="notifications")
    op.drop_index("notifications_created_idx", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("notification_subscriptions_filter_key", table_name="notification_subscriptions")
    op.drop_index("notification_subscriptions_exact_key", table_name="notification_subscriptions")
    op.drop_index("notification_subscriptions_user_idx", table_name="notification_subscriptions")
    op.drop_index("notification_subscriptions_subject_idx", table_name="notification_subscriptions")
    op.drop_table("notification_subscriptions")
    op.drop_table("notification_profiles")
    op.drop_constraint("domain_event_deliveries_claim_complete", "domain_event_deliveries", type_="check")
    op.drop_constraint("domain_event_deliveries_claim_count_nonnegative", "domain_event_deliveries", type_="check")
    op.drop_column("domain_event_deliveries", "claim_count")
    op.drop_column("domain_event_deliveries", "claim_token")


def _function(name: str) -> PGFunction:
    return next(
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, PGFunction) and entity.signature.partition("(")[0] == name
    )


def _trigger(name: str) -> PGTrigger:
    return next(
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, PGTrigger) and entity.signature.partition("(")[0] == name
    )
