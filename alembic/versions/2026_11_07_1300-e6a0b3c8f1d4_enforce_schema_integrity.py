"""Constrain build categories, object keys, and role inclusion graphs."""

from alembic import op

revision: str = "e6a0b3c8f1d4"
down_revision: str | None = "d5f9a2b7e0c3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO other_builds (build_id) SELECT id FROM builds WHERE category IS NULL ON CONFLICT DO NOTHING"
    )
    op.execute("UPDATE builds SET category = 'Other' WHERE category IS NULL")
    op.alter_column("builds", "category", nullable=False)
    op.create_check_constraint(
        "builds_category_check", "builds", "category IN ('Door','Extender','Utility','Entrance','Other')"
    )
    op.drop_index("idx_builds_category", table_name="builds")
    op.create_index("idx_builds_category", "builds", ["category"])
    op.create_foreign_key(
        "media_artifacts_object_key_fkey", "media_artifacts", "media_artifact_objects", ["object_key"], ["object_key"]
    )
    op.create_check_constraint(
        "schematic_files_object_key_check",
        "schematic_files",
        "object_key = 'schematics/' || left(sha256, 2) || '/' || sha256",
    )
    # Existing cycles have no valid interpretation; offline deployment permits dropping these edges.
    op.execute("DELETE FROM permission_role_includes")
    op.execute(
        "CREATE FUNCTION public.check_permission_role_include_cycle() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nBEGIN\n    -- Serialize graph checks; the next query gets a fresh READ COMMITTED snapshot.\n    -- Repeatable-read snapshots cannot see an edge committed while waiting.\n    IF current_setting('transaction_isolation') <> 'read committed' THEN\n        RAISE EXCEPTION 'role inclusion writes require READ COMMITTED isolation'\n            USING ERRCODE = '0A000';\n    END IF;\n    PERFORM pg_advisory_xact_lock(1936812391, 1);\n    IF EXISTS (\n        WITH RECURSIVE reachable(id) AS (\n            SELECT NEW.included_role_id\n            UNION\n            SELECT edge.included_role_id\n            FROM permission_role_includes edge JOIN reachable ON edge.role_id = reachable.id\n        )\n        SELECT 1 FROM reachable WHERE id = NEW.role_id\n    ) THEN\n        RAISE EXCEPTION 'permission role inclusion cycle'\n            USING ERRCODE = '23514', CONSTRAINT = 'permission_role_includes_acyclic';\n    END IF;\n    RETURN NEW;\nEND;\n$$;"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER permission_role_includes_acyclic AFTER INSERT OR UPDATE ON public.permission_role_includes FOR EACH ROW EXECUTE FUNCTION public.check_permission_role_include_cycle();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER permission_role_includes_acyclic ON public.permission_role_includes")
    op.execute("DROP FUNCTION public.check_permission_role_include_cycle()")
    op.drop_constraint("schematic_files_object_key_check", "schematic_files", type_="check")
    op.drop_constraint("media_artifacts_object_key_fkey", "media_artifacts", type_="foreignkey")
    op.drop_index("idx_builds_category", table_name="builds")
    op.execute("CREATE INDEX idx_builds_category ON builds (category) WHERE category IS NOT NULL")
    op.drop_constraint("builds_category_check", "builds", type_="check")
    op.alter_column("builds", "category", nullable=True)
