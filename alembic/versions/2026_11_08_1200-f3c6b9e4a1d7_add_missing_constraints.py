"""Add the missing build, schematic, version, queue, and verification-code constraints."""

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f3c6b9e4a1d7"
down_revision: str | None = "e2b5a8d3f0c6"
branch_labels: str | None = None
depends_on: str | None = None

_SUBTYPE_FUNCTION = "CREATE FUNCTION public.enforce_build_category_subtype() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_ids bigint[];\n    target_id bigint;\n    build_category text;\n    expected_table text;\n    door_count integer;\n    extender_count integer;\n    utility_count integer;\n    entrance_count integer;\n    other_count integer;\nBEGIN\n    -- Each build has exactly one row in the subtype table named by its category, which is what\n    -- SQLAlchemy's joined-table inheritance needs to load it. Deferred, because the ORM inserts\n    -- the builds row before the subtype row.\n    IF TG_TABLE_NAME = 'builds' THEN\n        target_ids := ARRAY[CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END];\n        IF TG_OP = 'UPDATE' AND OLD.id IS DISTINCT FROM NEW.id THEN\n            target_ids := array_append(target_ids, OLD.id);\n        END IF;\n    ELSE\n        target_ids := ARRAY[CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END];\n        IF TG_OP = 'UPDATE' AND OLD.build_id IS DISTINCT FROM NEW.build_id THEN\n            target_ids := array_append(target_ids, OLD.build_id);\n        END IF;\n    END IF;\n\n    FOREACH target_id IN ARRAY target_ids LOOP\n        -- See enforce_vote_session_kind_subtype for why NO KEY UPDATE.\n        SELECT category INTO build_category FROM public.builds WHERE id = target_id FOR NO KEY UPDATE;\n        IF NOT FOUND THEN\n            CONTINUE;\n        END IF;\n\n        SELECT\n            (SELECT count(*) FROM public.doors WHERE build_id = target_id),\n            (SELECT count(*) FROM public.extenders WHERE build_id = target_id),\n            (SELECT count(*) FROM public.utilities WHERE build_id = target_id),\n            (SELECT count(*) FROM public.entrances WHERE build_id = target_id),\n            (SELECT count(*) FROM public.other_builds WHERE build_id = target_id)\n        INTO door_count, extender_count, utility_count, entrance_count, other_count;\n\n        expected_table := CASE build_category\n            WHEN 'Door' THEN 'doors'\n            WHEN 'Extender' THEN 'extenders'\n            WHEN 'Utility' THEN 'utilities'\n            WHEN 'Entrance' THEN 'entrances'\n            WHEN 'Other' THEN 'other_builds'\n        END;\n\n        IF NOT (\n            door_count + extender_count + utility_count + entrance_count + other_count = 1\n            AND CASE expected_table\n                WHEN 'doors' THEN door_count\n                WHEN 'extenders' THEN extender_count\n                WHEN 'utilities' THEN utility_count\n                WHEN 'entrances' THEN entrance_count\n                WHEN 'other_builds' THEN other_count\n            END = 1\n        ) THEN\n            RAISE EXCEPTION USING\n                ERRCODE = 'check_violation',\n                MESSAGE = format(\n                    'build %s category %s has subtype counts doors=%s, extenders=%s, utilities=%s, '\n                    'entrances=%s, other_builds=%s',\n                    target_id, build_category, door_count, extender_count, utility_count, entrance_count,\n                    other_count\n                ),\n                SCHEMA = 'public',\n                TABLE = 'builds',\n                CONSTRAINT = 'builds_category_subtype_check';\n        END IF;\n    END LOOP;\n\n    RETURN NULL;\nEND;\n$$;"
_SUBTYPE_TRIGGERS = [
    "CREATE CONSTRAINT TRIGGER builds_category_subtype_check AFTER INSERT OR UPDATE OF id, category ON public.builds DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();",
    "CREATE CONSTRAINT TRIGGER doors_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.doors DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();",
    "CREATE CONSTRAINT TRIGGER extenders_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.extenders DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();",
    "CREATE CONSTRAINT TRIGGER utilities_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.utilities DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();",
    "CREATE CONSTRAINT TRIGGER entrances_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.entrances DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();",
    "CREATE CONSTRAINT TRIGGER other_builds_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.other_builds DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();",
]

# Queue checks that QueueColumns now adds to every queue table but these tables lacked.
_QUEUE_CHECKS = (
    ("discord_sync_queue", "attempts"),
    ("discord_sync_queue", "claim"),
    ("domain_event_deliveries", "attempts"),
    ("media_normalization_jobs", "claim"),
    ("record_recompute_queue", "attempts"),
    ("record_recompute_queue", "claim"),
    ("schematic_jobs", "attempts"),
    ("schematic_jobs", "claim"),
    ("schematic_render_queue", "attempts"),
    ("schematic_render_queue", "claim"),
    ("search_embedding_queue", "attempts"),
    ("search_embedding_queue", "claim"),
    ("search_projection_queue", "attempts"),
    ("search_projection_queue", "claim"),
)

_SCHEMATIC_CHECKS = (
    (
        "build_schematics_measurements_nonnegative",
        "width >= 0 AND height >= 0 AND length >= 0 AND allocated_width >= 0 AND allocated_height >= 0 "
        "AND allocated_length >= 0 AND block_count >= 0 AND entity_count >= 0 AND palette_size >= 0",
    ),
    ("build_schematics_bounding_volume_check", "bounding_volume = width::bigint * height * length"),
    ("build_schematics_block_count_check", "block_count <= bounding_volume"),
    (
        "build_schematics_tight_within_allocated",
        "width <= allocated_width AND height <= allocated_height AND length <= allocated_length",
    ),
    ("build_schematics_public_is_published", "visibility <> 'public_download' OR published_at IS NOT NULL"),
    (
        "build_schematics_withdrawn_after_published",
        "withdrawn_at IS NULL OR (published_at IS NOT NULL AND withdrawn_at >= published_at)",
    ),
)


def _queue_check(table: str, kind: str) -> tuple[str, str]:
    if kind == "attempts":
        return f"{table}_attempts_nonnegative", "attempts >= 0"
    return f"{table}_claim_complete", "(claimed_at IS NULL) = (claim_token IS NULL)"


def upgrade() -> None:
    # Offline deployment permits dropping builds whose subtype rows contradict their category.
    op.execute("""
        DELETE FROM builds b
        WHERE (SELECT count(*) FROM doors WHERE build_id = b.id)
            + (SELECT count(*) FROM extenders WHERE build_id = b.id)
            + (SELECT count(*) FROM utilities WHERE build_id = b.id)
            + (SELECT count(*) FROM entrances WHERE build_id = b.id)
            + (SELECT count(*) FROM other_builds WHERE build_id = b.id) <> 1
           OR NOT EXISTS (
                SELECT 1 FROM (
                    SELECT 'Door' AS category FROM doors WHERE build_id = b.id
                    UNION ALL SELECT 'Extender' FROM extenders WHERE build_id = b.id
                    UNION ALL SELECT 'Utility' FROM utilities WHERE build_id = b.id
                    UNION ALL SELECT 'Entrance' FROM entrances WHERE build_id = b.id
                    UNION ALL SELECT 'Other' FROM other_builds WHERE build_id = b.id
                ) subtype WHERE subtype.category = b.category
           )
    """)
    op.create_entity(PGFunction.from_sql(_SUBTYPE_FUNCTION))
    for sql in _SUBTYPE_TRIGGERS:
        op.create_entity(PGTrigger.from_sql(sql))

    for name, condition in _SCHEMATIC_CHECKS:
        op.create_check_constraint(name, "build_schematics", condition)
    for table, kind in _QUEUE_CHECKS:
        name, condition = _queue_check(table, kind)
        op.create_check_constraint(name, table, condition)

    op.create_unique_constraint(
        "versions_number_key", "versions", ["edition", "major_version", "minor_version", "patch_number"]
    )
    op.create_check_constraint("versions_edition_check", "versions", "edition IN ('Java', 'Bedrock')")
    op.create_check_constraint(
        "versions_number_check", "versions", "major_version >= 0 AND minor_version >= 0 AND patch_number >= 0"
    )

    # Only restrictions were ever written; anything else, or a facet whose tag is gone, is unreadable.
    op.execute("""
        DELETE FROM title_definition_facets f
        WHERE facet_kind <> 'restriction' OR NOT EXISTS (SELECT 1 FROM tag_definitions t WHERE t.id = f.facet_id)
    """)
    op.drop_constraint("title_definition_facets_kind_check", "title_definition_facets", type_="check")
    op.create_check_constraint(
        "title_definition_facets_kind_check", "title_definition_facets", "facet_kind = 'restriction'"
    )
    op.alter_column("title_definition_facets", "facet_id", type_=postgresql.BIGINT())
    op.create_foreign_key(
        "title_definition_facets_facet_id_fkey",
        "title_definition_facets",
        "tag_definitions",
        ["facet_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index("notifications_event_idx", "notifications", ["event_id"])

    # Keep only each player's newest code, then drop older duplicates of a valid code.
    op.execute("""
        DELETE FROM verification_codes v
        WHERE EXISTS (
            SELECT 1 FROM verification_codes newer
            WHERE newer.minecraft_uuid = v.minecraft_uuid AND (newer.created, newer.id) > (v.created, v.id)
        )
    """)
    op.execute("""
        DELETE FROM verification_codes v
        WHERE v.valid AND EXISTS (
            SELECT 1 FROM verification_codes newer
            WHERE newer.valid AND newer.code = v.code AND (newer.created, newer.id) > (v.created, v.id)
        )
    """)
    op.alter_column("verification_codes", "id", type_=postgresql.BIGINT())
    op.execute("ALTER SEQUENCE verification_codes_id_seq AS bigint")
    op.create_index(
        "verification_codes_valid_code_key",
        "verification_codes",
        ["code"],
        unique=True,
        postgresql_where="valid",
    )


def downgrade() -> None:
    op.drop_index("verification_codes_valid_code_key", table_name="verification_codes")
    # Rows above the smallint range cannot survive the narrower key.
    op.execute("DELETE FROM verification_codes WHERE id > 32767")
    op.execute("ALTER SEQUENCE verification_codes_id_seq AS smallint")
    op.alter_column("verification_codes", "id", type_=postgresql.SMALLINT())

    op.drop_index("notifications_event_idx", table_name="notifications")

    op.drop_constraint("title_definition_facets_facet_id_fkey", "title_definition_facets", type_="foreignkey")
    op.alter_column("title_definition_facets", "facet_id", type_=postgresql.INTEGER())
    op.drop_constraint("title_definition_facets_kind_check", "title_definition_facets", type_="check")
    op.create_check_constraint(
        "title_definition_facets_kind_check",
        "title_definition_facets",
        "facet_kind IN ('restriction', 'type', 'pattern', 'category')",
    )

    op.drop_constraint("versions_number_check", "versions", type_="check")
    op.drop_constraint("versions_edition_check", "versions", type_="check")
    op.drop_constraint("versions_number_key", "versions", type_="unique")

    for table, kind in _QUEUE_CHECKS:
        op.drop_constraint(_queue_check(table, kind)[0], table, type_="check")
    for name, _condition in _SCHEMATIC_CHECKS:
        op.drop_constraint(name, "build_schematics", type_="check")

    for sql in _SUBTYPE_TRIGGERS:
        op.drop_entity(PGTrigger.from_sql(sql))
    op.drop_entity(PGFunction.from_sql(_SUBTYPE_FUNCTION))
