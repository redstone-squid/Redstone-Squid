"""Retain linked images with their build links."""

from alembic import op

revision = "315f79ee0c2a"
down_revision = "c6a923d9bc70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE build_image_mirrors (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    build_id bigint NOT NULL,
    url text NOT NULL,
    attempts integer NOT NULL DEFAULT 1,
    retry_at timestamptz NOT NULL DEFAULT (now() + interval '10 minutes'),
    public_url text,
    content bytea,
    content_type text,
    UNIQUE (build_id, url),
    FOREIGN KEY (build_id, url) REFERENCES build_links(build_id,url) ON DELETE CASCADE
)""")


def downgrade() -> None:
    op.drop_table("build_image_mirrors")
