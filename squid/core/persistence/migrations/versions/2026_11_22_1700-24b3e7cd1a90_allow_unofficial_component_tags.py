"""Allow unofficial component tags in the build catalogue."""

from alembic import op

revision = "24b3e7cd1a90"
down_revision = "27ce93160bb2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The live catalogue cleanup installed this constraint before the next app rollout.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'public.tag_definitions'::regclass
                    AND conname = 'tag_definitions_user_showcase_only_check'
            ) THEN
                ALTER TABLE public.tag_definitions DROP CONSTRAINT tag_definitions_user_showcase_only_check;
                ALTER TABLE public.tag_definitions ADD CONSTRAINT tag_definitions_user_kind_check
                    CHECK (authority = 'official' OR (record_operator IS NULL AND
                        (semantic_kind = 'showcase' OR
                        (semantic_kind = 'restriction' AND restriction_type = 'component' AND value_type = 'none'))));
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.create_check_constraint(
        "tag_definitions_user_showcase_only_check",
        "tag_definitions",
        "authority = 'official' OR "
        "(semantic_kind = 'showcase' AND restriction_type IS NULL AND record_operator IS NULL)",
    )
    op.drop_constraint("tag_definitions_user_kind_check", "tag_definitions", type_="check")
