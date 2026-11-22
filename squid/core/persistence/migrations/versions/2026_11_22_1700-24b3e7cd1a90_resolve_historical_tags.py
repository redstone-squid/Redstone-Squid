"""Extend the build catalogue and resolve curated historical tag names."""

from alembic import op

revision = "24b3e7cd1a90"
down_revision = "27ce93160bb2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("tag_definitions_user_showcase_only_check", "tag_definitions", type_="check")
    op.create_check_constraint(
        "tag_definitions_user_kind_check",
        "tag_definitions",
        "authority = 'official' OR (record_operator IS NULL AND "
        "(semantic_kind = 'showcase' OR "
        "(semantic_kind = 'restriction' AND restriction_type = 'component' AND value_type = 'none')))",
    )
    op.execute("""
        WITH new_tags(stable_key, display_name, authority, semantic_kind, restriction_type, display_order) AS (
            VALUES
                ('glass', 'Glass', 'official', 'pattern', NULL, 43),
                ('infinitely_expandable', 'Infinitely Expandable', 'official', 'restriction', 'wiring-placement', 18),
                ('contained', 'Contained', 'official', 'restriction', 'component', 45),
                ('super_sync', 'Super Sync', 'official', 'restriction', 'animated', 46),
                ('full_sync', 'Full Sync', 'official', 'restriction', 'animated', 47),
                ('semi_sync', 'Semi Sync', 'official', 'restriction', 'animated', 48),
                ('shutter', 'Shutter', 'official', 'restriction', 'animated', 49),
                ('spiral', 'Spiral', 'official', 'restriction', 'animated', 50),
                ('cracked', 'Cracked', 'user', 'restriction', 'component', 51),
                ('no_burnouts', 'No Burnouts', 'user', 'restriction', 'component', 52),
                ('no_mobs', 'No Mobs', 'user', 'restriction', 'component', 53),
                ('no_redstone_dust', 'No Redstone Dust', 'user', 'restriction', 'component', 54),
                ('no_comparators', 'No Comparators', 'user', 'restriction', 'component', 55),
                ('no_jukeboxes', 'No Jukeboxes', 'user', 'restriction', 'component', 56),
                ('no_target_blocks', 'No Target Blocks', 'user', 'restriction', 'component', 57),
                ('no_hoppers', 'No Hoppers', 'user', 'restriction', 'component', 58),
                ('no_cauldrons', 'No Cauldrons', 'user', 'restriction', 'component', 59),
                ('no_droppers', 'No Droppers', 'user', 'restriction', 'component', 60),
                ('no_redstone_torches', 'No Redstone Torches', 'user', 'restriction', 'component', 61),
                ('no_repeaters', 'No Repeaters', 'user', 'restriction', 'component', 62),
                ('no_furnaces', 'No Furnaces', 'user', 'restriction', 'component', 63),
                ('no_redstone_blocks', 'No Redstone Blocks', 'user', 'restriction', 'component', 64),
                ('no_fence_gates', 'No Fence Gates', 'user', 'restriction', 'component', 65),
                ('no_pistons', 'No Pistons', 'user', 'restriction', 'component', 66),
                ('no_grindstones', 'No Grindstones', 'user', 'restriction', 'component', 67),
                ('uncontained', 'Uncontained', 'user', 'restriction', 'component', 68),
                ('only_piston_sounds', 'Only Piston Sounds', 'user', 'restriction', 'component', 69)
        )
        INSERT INTO tag_definitions (
            stable_key, display_name, normalized_name, authority, semantic_kind, restriction_type,
            value_type, record_operator, render_template, default_display_order, moderation_status
        )
        SELECT stable_key, display_name, lower(display_name), authority, semantic_kind, restriction_type,
            'none', CASE WHEN authority = 'official' AND semantic_kind = 'restriction' THEN 'present' END,
            '{name}', display_order, 'approved'
        FROM new_tags candidate
        WHERE NOT EXISTS (SELECT 1 FROM tag_definitions existing WHERE existing.stable_key = candidate.stable_key)
    """)
    op.execute("""
        INSERT INTO tag_applicabilities (tag_id, build_kind)
        SELECT tag.id, kind
        FROM tag_definitions tag
        CROSS JOIN (VALUES ('Door'), ('Entrance'), ('Extender'), ('Utility'), ('Other')) AS kinds(kind)
        WHERE tag.restriction_type = 'component'
            OR tag.stable_key IN ('flush', 'infinitely_expandable')
            OR (kind IN ('Door', 'Entrance', 'Extender', 'Utility')
                AND tag.stable_key IN ('full_seamless', 'quart_seamless'))
            OR (kind IN ('Door', 'Entrance') AND tag.stable_key IN ('sand', 'full_floor_hipster'))
            OR (kind = 'Door' AND tag.stable_key IN ('glass', 'super_sync', 'full_sync', 'semi_sync', 'shutter', 'spiral'))
        ON CONFLICT DO NOTHING
    """)
    # These curated names can move from the source field to the tag's canonical field.
    # Corrections cover historical spelling errors and claims that need the import's interpretation.
    op.execute("""
        CREATE TEMP TABLE tag_name_cleanup ON COMMIT DROP AS
        SELECT * FROM (VALUES
            ('full_seamless', 'seamless|seam', 'seemless|seemeless|seamlss|semless|seamlesss|fully seamless|flush seamless|seamless flush|flush/seamless|seam flush'),
            ('quart_seamless', '', 'unseamless|nonseamless|non-seamless|non seamless|unseam|nonseam|non seam|non-seam|no seamless|not seamless|non-seemless|not seemless|unseemless|unseem|unsealmless|unseamless+|super unseamless'),
            ('full_floor_hipster', 'hipster|hip|full hipster|floor hipster', '(full) hipster|full-hipster'),
            ('full_tnt', 'tnt', ''),
            ('dual_cave_corner', 'dual corner|double corner', ''),
            ('infinitely_expandable', 'inf expandable', 'infinite expandable|inf. expandable|infinite-expandable|infinitely-expandable|infinitily expandeable|infinititely expandable|infinitely expandeable|infinitely expendable|infinatly expandable|infinitey expandable|infintely expandable|infinitly expandeable|inf exp|inf expendable|inf-expandable|infi-expandable|infinity expandable'),
            ('no_gravity_blocks', 'sandless|no sand|sand-less', ''),
            ('glass', '', ''),
            ('shutter', '', ''),
            ('spiral', '', ''),
            ('cracked', '', ''),
            ('sand', '', ''),
            ('zomba', '', 'all zomba category restrictions|all of zomba category restrictions'),
            ('zombi', '', ''),
            ('redstone_block_only', '', ''),
            ('center_glass', 'glass center|centre glass|glass centre', ''),
            ('slab_shifted', 'slab shifted', ''),
            ('always_on_lamp', 'always-on lamp', ''),
            ('sissy_bar', 'sissybar|sissy-bar', ''),
            ('dual_rail', 'double rail', ''),
            ('semi_flush', 'semi-flush|semiflush', 'semi-*flush'),
            ('flush', 'fullflush', 'fush|flush seamless|seamless flush|flush/seamless|seam flush'),
            ('semi_seamless', 'semiseamless|semi seam', 'semi seamlesss|semi-seamelss'),
            ('super_seamless', 'superseamless|superseam', ''),
            ('not_locational', 'non-locational|non-loc|non locational|nonloc|non loc|nonlocational|not loc', ''),
            ('not_directional', 'non-directional|non-dir|non directional|non dir|nondir|nondirectional|not dir', ''),
            ('locational', 'loc', ''),
            ('directional', 'dir', ''),
            ('no_observers', 'observer-less|obs-less|observer less|no observer|without observers', 'obseverless|observeless'),
            ('no_entities', 'entity-less|entity less|no entity|0 entity|0 entities', 'entitiless|entitieless|entittyless'),
            ('no_slime_blocks', 'slime-less|no slimeblocks|no slime block|no slime|without slime|no slimes', ''),
            ('no_honey_blocks', 'honey-less|no honeyblocks|no honey block', ''),
            ('no_note_blocks', 'noteblock-less|no noteblocks|no note block', ''),
            ('no_clocks', 'clock-less|no clock', ''),
            ('no_sticky_pistons', 'sticky piston-less|sticky-pistonless|without sticky pistons', ''),
            ('no_flying_machines', 'flyingmachineless|flying-machine-less', 'flymachineless'),
            ('contained', 'fully contained', ''),
            ('full_sync', 'fully sync|fully synced|fully synchronized|fully synchronised|fully-sync', ''),
            ('super_sync', 'supersync', ''),
            ('semi_sync', 'semisync|semi-sync', ''),
            ('no_burnouts', 'burnoutless|no burnout', ''),
            ('no_mobs', 'mobless', ''),
            ('no_redstone_dust', 'dustless|no dust|redstone dustless', ''),
            ('no_comparators', 'comparatorless|comparator-less|no comparator', 'comperatorless'),
            ('no_jukeboxes', 'jukeless|jukeboxless', ''),
            ('no_target_blocks', 'targetless|target-less|targetblockless|no targets', ''),
            ('no_hoppers', 'hopperless', 'dropper-hopperless'),
            ('no_cauldrons', 'cauldronless', ''),
            ('no_droppers', 'dropperless', 'dropper-hopperless'),
            ('no_redstone_torches', 'torchless', ''),
            ('no_repeaters', 'repeaterless', ''),
            ('no_furnaces', 'furnaceless', ''),
            ('no_redstone_blocks', 'redstoneblockless', ''),
            ('no_fence_gates', 'fencegateless', ''),
            ('no_pistons', 'pistonless', ''),
            ('no_grindstones', 'grindstoneless', ''),
            ('uncontained', '', ''),
            ('only_piston_sounds', 'piston sounds only|piston sound only|only piston noises|no non-piston sounds|piston noise only', 'pistons sounds only|pisten sounds only')
        ) AS cleanup(stable_key, aliases, corrections)
    """)
    op.execute("""
        WITH inserted_aliases AS (
            INSERT INTO tag_aliases (tag_id, alias, normalized_alias)
            SELECT tag.id, name, name
            FROM tag_name_cleanup cleanup
            JOIN tag_definitions tag USING (stable_key)
            CROSS JOIN LATERAL string_to_table(cleanup.aliases, '|') AS names(name)
            WHERE name <> '' AND NOT EXISTS (
                SELECT 1 FROM tag_definitions other
                LEFT JOIN tag_aliases alias ON alias.tag_id = other.id
                WHERE other.id <> tag.id AND other.semantic_kind = tag.semantic_kind
                    AND (other.normalized_name = name OR alias.normalized_alias = name)
            )
            ON CONFLICT DO NOTHING
            RETURNING tag_id
        )
        UPDATE tag_definitions tag SET revision = revision + 1, updated_at = now()
        WHERE id IN (SELECT tag_id FROM inserted_aliases)
    """)
    op.execute("LOCK TABLE builds, build_tag_assignments IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""
        CREATE TEMP TABLE tag_backfill ON COMMIT DROP AS
        WITH names AS (
            SELECT tag.id AS tag_id, tag.normalized_name AS name
            FROM tag_name_cleanup cleanup JOIN tag_definitions tag USING (stable_key)
            UNION
            SELECT tag.id, name
            FROM tag_name_cleanup cleanup JOIN tag_definitions tag USING (stable_key)
            CROSS JOIN LATERAL string_to_table(cleanup.aliases || '|' || cleanup.corrections, '|') AS names(name)
            WHERE name <> ''
            UNION
            SELECT tag.id, name
            FROM tag_definitions tag LEFT JOIN tag_aliases alias ON alias.tag_id = tag.id
            CROSS JOIN LATERAL unnest(ARRAY[tag.normalized_name, alias.normalized_alias]) AS names(name)
            WHERE tag.restriction_type = 'component' OR tag.stable_key = 'flush'
        ),
        unresolved AS (
            SELECT build.id AS build_id, build.category, field, item, position
            FROM builds build
            CROSS JOIN LATERAL jsonb_each(
                CASE WHEN jsonb_typeof(extra_info->'unknown_restrictions') = 'object'
                    THEN extra_info->'unknown_restrictions' ELSE '{}'::jsonb END
            ) AS fields(field, items)
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(items) = 'array' THEN items ELSE '[]'::jsonb END
            ) WITH ORDINALITY AS entries(item, position)
            UNION ALL
            SELECT build.id, build.category, 'unknown_patterns', item, position
            FROM builds build
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(extra_info->'unknown_patterns') = 'array'
                    THEN extra_info->'unknown_patterns' ELSE '[]'::jsonb END
            ) WITH ORDINALITY AS entries(item, position)
        )
        SELECT input.build_id, input.field, input.position,
            array_agg(DISTINCT tag.id ORDER BY tag.id) AS tag_ids
        FROM unresolved input
        JOIN names ON names.name = lower(btrim(regexp_replace(item #>> '{}', '[[:space:]]+', ' ', 'g')))
        JOIN tag_definitions tag ON tag.id = names.tag_id
        JOIN tag_applicabilities applicability ON applicability.tag_id = tag.id
            AND applicability.build_kind = input.category
        WHERE jsonb_typeof(item) = 'string' AND tag.moderation_status = 'approved'
            AND NOT tag.withdrawn AND tag.value_type = 'none'
        GROUP BY input.build_id, input.field, input.position
        HAVING count(DISTINCT tag.id) = CASE
            WHEN min(names.name) IN ('dropper-hopperless', 'flush seamless', 'seamless flush', 'flush/seamless', 'seam flush')
                THEN 2 ELSE 1 END
    """)
    op.execute("CREATE INDEX ON tag_backfill (build_id, field, position)")
    op.execute("""
        SELECT public.begin_build_edit('{
            "operation": "recalculation", "operation_id": "af73428e-c99f-4511-9866-89366849af53",
            "source": "migration", "actor_system": "historical_tag_cleanup"
        }'::jsonb)
    """)
    op.execute("""
        INSERT INTO build_tag_assignments (build_id, tag_id, value_type, origin)
        SELECT DISTINCT build_id, unnest(tag_ids), 'none', 'legacy_import'
        FROM tag_backfill
        ORDER BY 1, 2
        ON CONFLICT (build_id, tag_id) DO NOTHING
    """)
    op.execute("""
        WITH affected AS (SELECT DISTINCT build_id FROM tag_backfill),
        cleaned AS (
            SELECT build.id,
                CASE WHEN jsonb_typeof(extra_info->'unknown_restrictions') = 'object' THEN
                    (SELECT jsonb_object_agg(field, remaining) FROM jsonb_each(extra_info->'unknown_restrictions')
                        AS fields(field, items)
                     CROSS JOIN LATERAL (
                         SELECT CASE WHEN jsonb_typeof(items) = 'array' THEN (
                             SELECT jsonb_agg(item ORDER BY position)
                             FROM jsonb_array_elements(items) WITH ORDINALITY AS entries(item, position)
                             WHERE NOT EXISTS (
                                 SELECT 1 FROM tag_backfill match
                                 WHERE match.build_id = build.id AND match.field = fields.field
                                     AND match.position = entries.position
                             )
                         ) ELSE items END AS remaining
                     ) AS preserved WHERE remaining IS NOT NULL)
                ELSE extra_info->'unknown_restrictions' END AS restrictions,
                CASE WHEN jsonb_typeof(extra_info->'unknown_patterns') = 'array' THEN
                    (SELECT jsonb_agg(item ORDER BY position)
                     FROM jsonb_array_elements(extra_info->'unknown_patterns') WITH ORDINALITY AS entries(item, position)
                     WHERE NOT EXISTS (
                         SELECT 1 FROM tag_backfill match
                         WHERE match.build_id = build.id AND match.field = 'unknown_patterns'
                             AND match.position = entries.position
                     ))
                ELSE extra_info->'unknown_patterns' END AS patterns
            FROM affected JOIN builds build ON build.id = affected.build_id
        )
        UPDATE builds build
        SET extra_info = (build.extra_info - 'unknown_restrictions' - 'unknown_patterns')
            || CASE WHEN cleaned.restrictions IS NULL THEN '{}'::jsonb
                ELSE jsonb_build_object('unknown_restrictions', cleaned.restrictions) END
            || CASE WHEN cleaned.patterns IS NULL THEN '{}'::jsonb
                ELSE jsonb_build_object('unknown_patterns', cleaned.patterns) END
        FROM cleaned WHERE build.id = cleaned.id
    """)
    op.execute("SELECT public.close_build_edit()")


def downgrade() -> None:
    message = "The historical tag migration merged raw names into assignments. Restore a backup to undo it."
    raise RuntimeError(message)
