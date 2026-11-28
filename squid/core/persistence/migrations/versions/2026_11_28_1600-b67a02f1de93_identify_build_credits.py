"""Give a build credit a stable identity independent of display order."""

from alembic import op

revision = "b67a02f1de93"
down_revision = "4eb15ae3d92c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE build_credits
        ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid(),
        DROP CONSTRAINT build_credits_pkey,
        ADD CONSTRAINT build_credits_pkey PRIMARY KEY (id),
        ADD CONSTRAINT build_credits_position_key UNIQUE (build_id, position) DEFERRABLE INITIALLY DEFERRED
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE build_credits
        DROP CONSTRAINT build_credits_position_key,
        DROP CONSTRAINT build_credits_pkey,
        DROP COLUMN id,
        ADD CONSTRAINT build_credits_pkey PRIMARY KEY (build_id, position)
    """)
