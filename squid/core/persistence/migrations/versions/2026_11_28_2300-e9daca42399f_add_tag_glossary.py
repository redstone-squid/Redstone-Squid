"""Store glossary descriptions and links to catalogue rules."""

from alembic import op

revision = "e9daca42399f"
down_revision = "d56712ca90bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tag_definitions ADD COLUMN description text, ADD COLUMN rules_anchor text")
    op.execute("""
        UPDATE tag_definitions t SET rules_anchor = '/rules#2.1-piston-doors'
        WHERE authority = 'official' AND EXISTS (
            SELECT 1 FROM tag_applicabilities a WHERE a.tag_id = t.id AND a.build_kind = 'door'
        )
    """)
    op.execute("""
        UPDATE tag_definitions t SET description = g.description
        FROM (VALUES
            ('glass', 'The door blocks are glass blocks. Glass panes do not qualify.'),
            ('sand', 'The door blocks are sand, gravel, or concrete powder.'),
            ('glass stripe', 'The center row of door blocks is glass.'),
            ('always on lamp', 'The lamp stays lit in both the open and closed states.')
        ) AS g(name, description)
        WHERE t.normalized_name = g.name AND t.semantic_kind = 'pattern' AND t.rules_anchor IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE tag_definitions DROP COLUMN rules_anchor, DROP COLUMN description")
