"""Write the documented table and column comments the models already carry.

Revision ID: e7d3a6c9f4b2
Revises: d6c2f5b8e3a9
Create Date: 2026-08-18 16:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7d3a6c9f4b2"
down_revision: str | None = "d6c2f5b8e3a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Comments are generated from model docstrings, so these drifted the moment a
# docstring was written or reworded without a migration to carry it across.
_COLUMN_COMMENTS: tuple[tuple[str, str, str], ...] = (
    (
        "api_keys",
        "secret_hash",
        "HMAC-SHA256 digest of the unrecoverable token secret.\n\nKeyed by a deployment pepper that never reaches this database; see\n`docs/credential-hashing.md`.",
    ),
    (
        "discord_sync_queue",
        "available_at",
        "When this row next becomes claimable, and the only column backoff writes.\n\nKept separate from `enqueued_at` so a repeatedly failing row keeps its place in\nFIFO order and still reports its true age to the queue-health gauges.",
    ),
    (
        "discord_sync_queue",
        "claim_token",
        "The database-minted fencing token handed to the worker that claimed this row.\n\nNullable for now so code from the previous release, which stamps only\n`claimed_at`, can keep draining the queue across a deploy window.",
    ),
    (
        "idempotency_requests",
        "principal",
        "The caller namespace a key is reserved in.\n\nThe application layer calls this the *caller*; the column keeps the older\nword because renaming it needs a migration, a rewrite of the unique index it\nanchors, and a redeploy window, for a name no client ever sees. The same\ntrade applies to the `principal` partition in `RateLimit-Policy` and to\n`SQUID_API_RATE_LIMIT_PRINCIPAL_REQUESTS`, both of which deployments and\nclients can observe. If the ban is meant repo-wide, that is its own commit.",
    ),
    (
        "record_recompute_queue",
        "available_at",
        "When this row next becomes claimable, and the only column backoff writes.",
    ),
    (
        "record_recompute_queue",
        "claim_token",
        "The database-minted fencing token handed to the worker that leased this scope.\n\nScopes are leased in batches and acknowledged as a set, so the token is what\ntells a finishing worker's acknowledgement from work enqueued during its run.",
    ),
    ("schematic_jobs", "claim_token", "The database-minted fencing token handed to the worker that claimed this row."),
    (
        "schematic_render_queue",
        "available_at",
        "When this row next becomes claimable, and the only column backoff writes.",
    ),
    (
        "schematic_render_queue",
        "claim_token",
        "The database-minted fencing token handed to the worker that claimed this row.",
    ),
    (
        "search_embedding_queue",
        "available_at",
        "When this row next becomes claimable, and the only column backoff writes.",
    ),
    (
        "search_embedding_queue",
        "claim_token",
        "The database-minted fencing token handed to the worker that claimed this row.",
    ),
    (
        "search_projection_queue",
        "available_at",
        "When this row next becomes claimable, and the only column backoff writes.",
    ),
    (
        "search_projection_queue",
        "claim_token",
        "The database-minted fencing token handed to the worker that claimed this row.",
    ),
)

_NOTIFICATION_PROFILES_COMMENT = 'Independent notification channel preferences.\n\nCarries no consent receipt: notifications are covered by the one privacy notice, whose\nreceipt lives on `accounts`. A row here means "these switches", not "this person agreed".'


def upgrade() -> None:
    for table, column, comment in _COLUMN_COMMENTS:
        op.alter_column(table, column, comment=comment)
    op.create_table_comment("notification_profiles", _NOTIFICATION_PROFILES_COMMENT)


def downgrade() -> None:
    for table, column, _ in _COLUMN_COMMENTS:
        op.alter_column(table, column, comment=None)
    op.drop_table_comment("notification_profiles")
