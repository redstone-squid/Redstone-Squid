"""Add the durable domain-event log.

Revision ID: e8a4b1d7c390
Revises: d7f3a9c5e164
Create Date: 2026-08-09 10:00:00+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "e8a4b1d7c390"
down_revision: str | Sequence[str] | None = "d7f3a9c5e164"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION_NAMES = {"emit_domain_event"}
_TRIGGER_NAMES = {"builds_emit_domain_event", "vote_sessions_emit_domain_event"}

_CONSUMERS = ("discord",)


def upgrade() -> None:
    """Create the event log, its consumers and deliveries, and the emit triggers."""
    op.create_table(
        "domain_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("aggregate_kind", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        comment="One state transition, recorded once and never coalesced.",
    )
    op.create_index("domain_events_aggregate_idx", "domain_events", ["aggregate_kind", "aggregate_id"], unique=False)

    consumers = op.create_table(
        "domain_event_consumers",
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
        comment="A registered reader of the event log.",
    )
    op.bulk_insert(consumers, [{"name": name} for name in _CONSUMERS])

    op.create_table(
        "domain_event_deliveries",
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["domain_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["consumer"], ["domain_event_consumers.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "consumer"),
        comment="One consumer's outstanding delivery of one event.",
    )
    op.create_index(
        "domain_event_deliveries_ready_idx",
        "domain_event_deliveries",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("claimed_at IS NULL"),
    )

    for entity in _selected_entities(PGFunction, _FUNCTION_NAMES):
        op.execute(entity.to_sql_statement_create())
    for entity in _selected_entities(PGTrigger, _TRIGGER_NAMES):
        op.execute(entity.to_sql_statement_create())


def downgrade() -> None:
    """Remove the emit triggers, function, deliveries, consumers, and event log."""
    for entity in reversed(_selected_entities(PGTrigger, _TRIGGER_NAMES)):
        op.execute(entity.to_sql_statement_drop())
    for entity in reversed(_selected_entities(PGFunction, _FUNCTION_NAMES)):
        op.execute(entity.to_sql_statement_drop())
    op.drop_index("domain_event_deliveries_ready_idx", table_name="domain_event_deliveries")
    op.drop_table("domain_event_deliveries")
    op.drop_table("domain_event_consumers")
    op.drop_index("domain_events_aggregate_idx", table_name="domain_events")
    op.drop_table("domain_events")


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(entity_type: type[EntityT], names: set[str]) -> list[EntityT]:
    return [
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, entity_type) and entity.signature.partition("(")[0] in names
    ]
