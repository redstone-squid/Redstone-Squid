"""Store build server directions in their own columns.

Server address, coordinates, and teleport command are ordinary user-edited facts, but they lived in
`extra_info`, which is meant for data we cannot process yet. Move them to columns on `builds` and
include them in build history snapshots.

The copy runs with edit capture disabled: the facts only move, so it should not appear as an edit
in each build's history.

Revision ID: 3c8e1f5a9d27
Revises: f4b7c28a610d
Create Date: 2026-11-22 15:00:00+00:00
"""

import sqlalchemy as sa

from alembic import op

revision: str = "3c8e1f5a9d27"
down_revision: str | None = "f4b7c28a610d"
branch_labels: str | None = None
depends_on: str | None = None

_NEW_SNAPSHOT = "CREATE OR REPLACE FUNCTION public.build_history_snapshot_v1(target_build_id bigint) RETURNS jsonb\n    LANGUAGE sql STABLE\n    SET timezone = 'UTC'\n    AS $$\nSELECT jsonb_build_object(\n    'build', jsonb_build_object('submission_status', b.submission_status, 'extra_info', b.extra_info, 'width', b.width, 'height', b.height, 'depth', b.depth, 'completion_time', b.completion_time, 'submission_time', b.submission_time, 'category', b.category, 'ai_generated', b.ai_generated, 'version_spec', b.version_spec, 'completion_at', b.completion_at, 'completion_evidence', b.completion_evidence, 'description', b.description, 'submitter_account_id', b.submitter_account_id, 'display_name', b.display_name, 'server_address', b.server_address, 'server_coordinates', b.server_coordinates, 'server_command', b.server_command, 'source_submission_draft_id', b.source_submission_draft_id, 'sponsor_installation_id', b.sponsor_installation_id, 'sponsor_display_name', b.sponsor_display_name, 'sponsor_address', b.sponsor_address, 'sponsor_description', b.sponsor_description, 'sponsor_website_url', b.sponsor_website_url),\n    'doors', (SELECT jsonb_build_object('orientation', d.orientation, 'door_width', d.door_width, 'door_height', d.door_height, 'door_depth', d.door_depth, 'expansion', d.expansion, 'normal_opening_time', d.normal_opening_time, 'normal_closing_time', d.normal_closing_time, 'visible_opening_time', d.visible_opening_time, 'visible_closing_time', d.visible_closing_time) FROM public.doors d WHERE d.build_id = b.id),\n    'extenders', (SELECT jsonb_build_object('orientation', d.orientation, 'extension_length', d.extension_length, 'extender_type', d.extender_type, 'extension_time', d.extension_time, 'retraction_time', d.retraction_time) FROM public.extenders d WHERE d.build_id = b.id),\n    'creators', COALESCE((SELECT jsonb_agg(jsonb_build_object('alias_id', a.id, 'name', a.name, 'account_id', a.account_id) ORDER BY a.id) FROM public.build_credits c JOIN public.creator_aliases a ON a.id = c.alias_id WHERE c.build_id = b.id), '[]'::jsonb),\n    'credits', COALESCE((SELECT jsonb_agg(jsonb_build_object('kind', c.kind, 'name', c.name, 'username', c.username, 'subject', c.subject, 'role', c.role, 'attribution', c.attribution, 'source_message_id', c.source_message_id, 'alias_id', c.alias_id) ORDER BY c.position) FROM public.build_credits c WHERE c.build_id = b.id), '[]'::jsonb),\n    'versions', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', v.id, 'edition', v.edition, 'major_version', v.major_version, 'minor_version', v.minor_version, 'patch_number', v.patch_number, 'data_version', v.data_version) ORDER BY v.id) FROM public.build_versions bv JOIN public.versions v ON v.id = bv.version_id WHERE bv.build_id = b.id), '[]'::jsonb),\n    'tags', COALESCE((SELECT jsonb_agg(jsonb_build_object('tag_id', r.tag_id, 'value_type', r.value_type, 'numeric_value', r.numeric_value::text, 'text_value', r.text_value, 'boolean_value', r.boolean_value, 'display_unit_key', r.display_unit_key, 'display_order', r.display_order, 'evidence', r.evidence, 'origin', r.origin, 'created_by_account_id', r.created_by_account_id) ORDER BY r.tag_id) FROM public.build_tag_assignments r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'links', COALESCE((SELECT jsonb_agg(jsonb_build_object('url', r.url, 'media_type', r.media_type) ORDER BY r.media_type, r.url) FROM public.build_links r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'source_messages', COALESCE((SELECT jsonb_agg(jsonb_build_object('message_id', r.message_id) ORDER BY r.position, r.message_id) FROM public.build_source_messages r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'schematics', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', r.id, 'file_sha256', r.file_sha256, 'is_primary', r.is_primary, 'original_filename', r.original_filename, 'uploaded_by_account_id', r.uploaded_by_account_id, 'visibility', r.visibility, 'license_code', r.license_code, 'rights_attested_at', r.rights_attested_at, 'rights_attested_by_account_id', r.rights_attested_by_account_id, 'published_at', r.published_at, 'withdrawn_at', r.withdrawn_at) ORDER BY r.id) FROM public.build_schematics r  WHERE r.build_id = b.id), '[]'::jsonb)\n) FROM public.builds b WHERE b.id = target_build_id;\n$$;\n"
_SERVER_SNAPSHOT_KEYS = (
    "'server_address', b.server_address, 'server_coordinates', b.server_coordinates, "
    "'server_command', b.server_command, "
)
_OLD_SNAPSHOT = _NEW_SNAPSHOT.replace(_SERVER_SNAPSHOT_KEYS, "")


def upgrade() -> None:
    op.add_column(
        "builds",
        sa.Column(
            "server_address", sa.Text(), nullable=True, comment="Minecraft server where players can visit the build."
        ),
    )
    op.add_column("builds", sa.Column("server_coordinates", sa.Text(), nullable=True))
    op.add_column(
        "builds",
        sa.Column(
            "server_command",
            sa.Text(),
            nullable=True,
            comment="Command that takes a player to the build on that server.",
        ),
    )
    op.execute(_NEW_SNAPSHOT)
    op.execute("ALTER TABLE public.builds DISABLE TRIGGER builds_capture_edit")
    op.execute(
        """
        UPDATE public.builds
        SET server_address = NULLIF(extra_info #>> '{server_info,server_ip}', ''),
            server_coordinates = NULLIF(extra_info #>> '{server_info,coordinates}', ''),
            server_command = NULLIF(extra_info #>> '{server_info,command_to_build}', ''),
            extra_info = extra_info - 'server_info'
        WHERE extra_info ? 'server_info'
        """
    )
    op.execute("ALTER TABLE public.builds ENABLE TRIGGER builds_capture_edit")


def downgrade() -> None:
    op.execute("ALTER TABLE public.builds DISABLE TRIGGER builds_capture_edit")
    op.execute(
        """
        UPDATE public.builds
        SET extra_info = extra_info || jsonb_build_object(
            'server_info',
            jsonb_strip_nulls(
                jsonb_build_object(
                    'server_ip', server_address,
                    'coordinates', server_coordinates,
                    'command_to_build', server_command
                )
            )
        )
        WHERE num_nonnulls(server_address, server_coordinates, server_command) > 0
        """
    )
    op.execute("ALTER TABLE public.builds ENABLE TRIGGER builds_capture_edit")
    op.execute(_OLD_SNAPSHOT)
    op.drop_column("builds", "server_command")
    op.drop_column("builds", "server_coordinates")
    op.drop_column("builds", "server_address")
