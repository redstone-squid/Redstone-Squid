"""Index the foreign keys deletes scan

PostgreSQL indexes the referenced side of a foreign key and never the referencing side, so every
`ON DELETE CASCADE`, `SET NULL` or `RESTRICT` has to find its children by whatever index the
referencing table happens to have. Fifty-two foreign keys in this schema had none whose leading
columns matched.

`accounts` is the case that matters. Twenty-two constraints point at it -- seven CASCADE, ten
SET NULL, five RESTRICT -- and not one of them was indexed, so deleting a single account meant
twenty-two sequential scans, including over `builds`, `votes` and `permission_audit_log`. That is
the account-erasure path, which is the one path that must stay affordable as the tables grow.

The rest of the additions follow the same rule, for the parents that actually get deleted: builds,
permission roles, guilds, and starboard origin messages. Two are query-motivated rather than
delete-motivated -- `build_creators.alias_id` answers "builds credited to this creator" and
`build_versions.version_id` answers "builds on this version", both of which are user-facing reads
that were doing a full scan of a junction table.

Deliberately not indexed: the foreign keys onto `versions`, `tag_units`, `record_rulesets`,
`record_competitions`, `messages` and `domain_event_consumers`. Those are RESTRICT references to
reference data that is written once and never deleted, so the scan they would avoid does not
happen, and an index there is pure write cost. `build_source_messages.message_id` is in that group
too: a message row is a retained fact that outlives the builds pointing at it.

Built with a plain `CREATE INDEX` inside the migration transaction. These are applied offline, so
the `ACCESS EXCLUSIVE` lock costs nothing that matters, and taking it is strictly better than
`CONCURRENTLY` here: one table pass instead of two, and no way to leave an `INVALID` index behind
if a statement fails.

Revision ID: a8c2d5f0e7b4
Revises: f7b1c4e9d6a3
Create Date: 2026-08-17 12:30:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a8c2d5f0e7b4"
down_revision: str | Sequence[str] | None = "f7b1c4e9d6a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES: tuple[tuple[str, str, str], ...] = (
    # Referenced by `accounts`.
    ("api_keys_owner_idx", "api_keys", "owner_account_id"),
    ("api_keys_created_by_idx", "api_keys", "created_by_account_id"),
    ("build_schematics_uploaded_by_idx", "build_schematics", "uploaded_by_account_id"),
    ("build_schematics_rights_attested_by_idx", "build_schematics", "rights_attested_by_account_id"),
    ("build_tag_assignments_created_by_idx", "build_tag_assignments", "created_by_account_id"),
    ("builds_submitter_idx", "builds", "submitter_account_id"),
    ("cli_device_enrollments_approved_by_idx", "cli_device_enrollments", "approved_by_account_id"),
    ("creator_alias_claims_account_idx", "creator_alias_claims", "account_id"),
    ("creator_alias_claims_resolved_by_idx", "creator_alias_claims", "resolved_by_account_id"),
    ("creator_aliases_account_idx", "creator_aliases", "account_id"),
    ("minecraft_player_challenges_approved_by_idx", "minecraft_player_challenges", "approved_by_account_id"),
    ("notification_deliveries_account_idx", "notification_deliveries", "account_id"),
    ("permission_audit_log_actor_idx", "permission_audit_log", "actor_account_id"),
    ("permission_grants_granted_by_idx", "permission_grants", "granted_by_account_id"),
    ("permission_role_assignments_granted_by_idx", "permission_role_assignments", "granted_by_account_id"),
    ("permission_role_includes_added_by_idx", "permission_role_includes", "added_by_account_id"),
    ("permission_role_patterns_added_by_idx", "permission_role_patterns", "added_by_account_id"),
    ("permission_roles_created_by_idx", "permission_roles", "created_by_account_id"),
    ("public_creator_redirects_target_idx", "public_creator_redirects", "target_account_id"),
    ("tag_definitions_created_by_idx", "tag_definitions", "created_by_account_id"),
    ("vote_sessions_author_idx", "vote_sessions", "author_account_id"),
    ("votes_account_idx", "votes", "account_id"),
    ("web_sessions_account_idx", "web_sessions", "account_id"),
    # Referenced by `builds`.
    ("build_vote_sessions_build_idx", "build_vote_sessions", "build_id"),
    ("record_holder_history_build_idx", "record_holder_history", "build_id"),
    ("record_recompute_queue_build_idx", "record_recompute_queue", "build_id"),
    ("record_results_provisional_build_idx", "record_results", "provisional_build_id"),
    # Referenced by `permission_roles`.
    ("permission_role_assignments_role_idx", "permission_role_assignments", "role_id"),
    ("permission_role_includes_included_idx", "permission_role_includes", "included_role_id"),
    # Referenced by `server_settings`.
    ("generic_vote_sessions_guild_idx", "generic_vote_sessions", "guild_id"),
    ("messages_guild_idx", "messages", "guild_id"),
    ("starboard_origin_messages_guild_idx", "starboard_origin_messages", "guild_id"),
    ("starboard_sources_guild_idx", "starboard_sources", "guild_id"),
    # Referenced by `starboard_origin_messages`.
    ("starboard_entries_origin_message_idx", "starboard_entries", "origin_message_id"),
    # Referenced by `tag_definitions`.
    ("tag_relations_target_idx", "tag_relations", "target_tag_id"),
    # Read paths rather than delete paths.
    ("build_creators_alias_idx", "build_creators", "alias_id"),
    ("build_versions_version_idx", "build_versions", "version_id"),
)
"""Every index this revision manages, as `(index, table, column)`.

The downgrade drives the same list, so an index cannot be added here and forgotten there.
"""


def upgrade() -> None:
    """Apply this revision."""
    for index, table, column in INDEXES:
        op.create_index(index, table, [column])


def downgrade() -> None:
    """Revert this revision."""
    for index, table, _column in INDEXES:
        op.drop_index(index, table_name=table)
