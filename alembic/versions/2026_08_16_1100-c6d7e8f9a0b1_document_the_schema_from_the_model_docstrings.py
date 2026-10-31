"""Document the schema from the model docstrings.

`Base.__init_subclass__` has always meant to turn an attribute docstring into that column's
`COMMENT`, but it read the class attribute after SQLAlchemy had replaced each `mapped_column()`
with an `InstrumentedAttribute`, which does not expose its `Column`. Every lookup missed and the
pass silently did nothing, so the only documented columns were the three carrying an explicit
`comment=`. The same `hasattr` mistake let a joined-inheritance subclass find its parent's
`__table_args__` and leave its own table undocumented.

This revision carries the comments the fixed pass now produces into the database, so `alembic
check` is clean again: 68 column comments and the five build-subclass table comments.

Revision ID: c6d7e8f9a0b1
Revises: b2c3d4e5f6a8, e5f6a7b8c9d2
Create Date: 2026-08-16 11:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: str | Sequence[str] | None = ("b2c3d4e5f6a8", "e5f6a7b8c9d2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_COMMENTS: tuple[tuple[str, str], ...] = (
    ("doors", "A door build with specific dimensions and timing information."),
    ("entrances", "An entrance build."),
    ("extenders", "An extender build."),
    ("other_builds", "A build which does not fit one of the structured catalogue categories."),
    ("utilities", "A utility build."),
)
"""Joined-inheritance subclasses of `builds`, which never received their own table comment."""

COLUMN_COMMENTS: tuple[tuple[str, str, str], ...] = (
    ("api_keys", "created_at", "When the credential was created."),
    ("api_keys", "created_by_account_id", "Account that created the credential, when known."),
    ("api_keys", "expires_at", "Optional instant after which authentication is rejected."),
    ("api_keys", "id", "Internal identifier unrelated to the public token key ID."),
    ("api_keys", "key_id", "Public lookup portion of the API token."),
    ("api_keys", "label", "Human-readable description of the credential's owner or purpose."),
    ("api_keys", "last_used_at", "Most recent throttled usage timestamp."),
    ("api_keys", "last_used_ip", "IP address associated with the most recent recorded use."),
    ("api_keys", "owner_account_id", "Optional account responsible for the credential."),
    ("api_keys", "revoked_at", "When the credential was revoked, or ``None`` while active."),
    ("api_keys", "scopes", "Capabilities granted to this credential."),
    ("api_keys", "secret_hash", "HMAC-SHA256 digest of the unrecoverable token secret."),
    (
        "build_schematics",
        "allocated_width",
        "Width of the region the file allocates, which can far exceed the tight content.",
    ),
    (
        "build_schematics",
        "analysis_schema_version",
        "Which revision of *our* analysis produced this row, bumped when we change what we read.",
    ),
    (
        "build_schematics",
        "analyzer_version",
        "Which engine build produced the fingerprints on this row, e.g. `nucleation-0.9.2`.",
    ),
    (
        "build_schematics",
        "block_count",
        "Non-air block count. **Not** the Door Rules cumulative volume, which counts air pockets\nand carries hallway, frame, and hitbox exceptions no static read can apply.",
    ),
    (
        "build_schematics",
        "bounding_volume",
        "Tight bounding box volume including air. Materialised so it can be range-scanned.",
    ),
    (
        "build_schematics",
        "file_sha256",
        "The stored bytes this analysis describes. `RESTRICT` because several builds can share\none file and losing it would strand every analysis that references it.",
    ),
    ("build_schematics", "fingerprint_exact", "Material- and orientation-sensitive identity, the strict tier."),
    (
        "build_schematics",
        "fingerprint_shape",
        "Translation- and rotation-invariant identity. This is the primary duplicate index.",
    ),
    (
        "build_schematics",
        "fingerprint_structural",
        "Coarse translation-invariant bucket. A build differing by a single block still matches,\nso this is a pre-filter feeding pairwise ranking and never a duplicate verdict by itself.",
    ),
    (
        "build_schematics",
        "is_primary",
        "Whether this is the schematic shown on the build card and used for duplicate checks.",
    ),
    ("build_schematics", "lattice", "The highest-coverage repeating unit cell found, if the build has one."),
    (
        "build_schematics",
        "original_filename",
        "The name the uploader's file had, kept for display only. Never trusted for typing.",
    ),
    ("build_schematics", "palette_size", "How many distinct block states the file declares."),
    (
        "build_schematics",
        "signature_structural",
        "The engine's structural signature document, kept for pre-filter experiments.",
    ),
    ("build_schematics", "signs", "Sign text recovered from the schematic, as `{x, y, z, text}` objects."),
    (
        "build_schematics",
        "simulation_evidence",
        "Staff-triggered tick-engine evidence. It never changes the build's declared timing.",
    ),
    (
        "build_schematics",
        "source_data_version",
        "The Minecraft data version the file declares, or `None` when it declares none.",
    ),
    (
        "build_schematics",
        "visibility",
        "Explicit download choice. Existing attachments remain private until re-attested.",
    ),
    ("build_schematics", "width", "Tight content width. Machine-read, unlike the human-declared value on `builds`."),
    (
        "build_source_messages",
        "position",
        "Submission order, so the first message stays identifiable as the request itself.",
    ),
    (
        "builds",
        "source_submission_draft_id",
        "Stable finalization key retained even if the short-lived source draft is later pruned.",
    ),
    ("builds", "sponsor_installation_id", "Opaque Paper installation ID snapshotted without a mutable foreign key."),
    ("discord_posts", "applied_revision", "The queue generation this post was last rendered at."),
    ("discord_posts", "channel_id", "Denormalised from `messages.channel_id`: a unique index cannot span a join."),
    (
        "discord_posts",
        "suppressed_at",
        "Set when the post was deleted outside the bot. Renderers choose whether to repost.",
    ),
    (
        "discord_sync_queue",
        "generation",
        "A globally monotonic staleness token, not a per-row counter.\n\nAcknowledging a job deletes its queue row, so a counter restarted at 1 on the next\nenqueue and could name a revision below one a post had already applied. Sequence\nvalues survive that because they are never rolled back or reused.",
    ),
    (
        "generic_vote_sessions",
        "guild_id",
        "The guild whose emoji palette the poll was drafted against.\n\nNullable so a poll can be created by a transport that has no guild -- the REST API\nor a standalone draft -- and have its presentation messages attached afterwards.",
    ),
    ("messages", "content", "Never exposed through the API. Retained for offline build inference and edit views."),
    ("messages", "created_at", "When Discord created the message, denormalised from the snowflake."),
    ("messages", "deleted_at", "Set when Discord reports the message gone. The row is a retained fact, never erased."),
    ("messages", "edited_at", "When `content` was last refreshed from a Discord edit."),
    ("messages", "guild_id", "The guild the message was sent in, or NULL in DMs."),
    ("messages", "id", "The Discord snowflake. Never generated here; the message exists before the row."),
    ("messages", "observed_at", "When the bot first recorded this message."),
    (
        "permission_audit_log",
        "action",
        "What happened: `grant`, `revoke`, `role_create`, `role_pattern_add`, and so on.",
    ),
    (
        "permission_audit_log",
        "reason",
        "Mandatory for `forbid`, enforced by the service rather than the schema so\nthe column stays usable for the actions that do not need it.",
    ),
    (
        "permission_grants",
        "granted_by_account_id",
        "Who issued this, or NULL when the system did: a migration backfill or the\nowner recovery CLI has no human grantor, and inventing one would put a\nfictional account into the audit trail.",
    ),
    (
        "permission_grants",
        "scope_guild_id",
        "Where the rule applies, or NULL for everywhere. A guild-scoped rule can\nnever satisfy a global node; the resolver checks that against the node's\ndeclared scope, so nodes added later are safe under old grants.",
    ),
    ("permission_grants", "subject_guild_id", "The guild the subject Discord role lives in."),
    ("permission_grants", "subject_role_id", "A Discord role snowflake, when the rule is attached to a role."),
    (
        "permission_role_assignments",
        "granted_by_account_id",
        "Who issued this, or NULL when the system did. See `PermissionGrant`.",
    ),
    (
        "permission_role_patterns",
        "mode",
        "1 to include, -1 to subtract. One mode per pattern, so a role cannot\ncontradict itself.",
    ),
    ("permission_role_patterns", "pattern", "A node name, a `*`/`**` wildcard, or an `@tag` selector."),
    ("permission_roles", "builtin_key", "Identifies a role whose patterns are defined in code rather than here."),
    ("permission_roles", "guild_id", "The guild owning this role, or NULL for a global one."),
    ("permission_roles", "name", "Display name."),
    ("permission_roles", "protected", "Refuses structural edits from anyone but the bot owner."),
    (
        "permission_roles",
        "rank",
        "Management hierarchy only: who may edit whom. Deliberately absent from\npermission resolution, so reordering roles can never change an authorization\noutcome. Enforced by property P10.",
    ),
    ("permission_roles", "slug", "Stable handle used in commands, unique within the owning guild."),
    ("schematic_files", "byte_size", "Object payload size used to bound downloads."),
    ("schematic_files", "sha256", "Lowercase hex SHA-256 of the object payload, and the identity of this row."),
    ("schematic_files", "source_format", "The format the content sniffer identified, e.g. `litematic`."),
    (
        "schematic_renders",
        "recipe_hash",
        "SHA-256 of the pack, camera recipe, output dimensions, and analyzer version.",
    ),
    (
        "server_settings",
        "locale",
        'Admin-configured language override, e.g. "en" or "zh-CN". Falls back to Discord\'s guild/user locale when unset.',
    ),
    (
        "submission_drafts",
        "source_installation_id",
        "Server-derived Paper installation retained independently of credential generations.",
    ),
    (
        "versions",
        "data_version",
        "Minecraft's own numeric world-format version, needed to retarget a schematic at this\nrelease. Nullable because Bedrock has no equivalent and Java releases predating the field\nhave none either.",
    ),
)
"""Every column whose model attribute carries a docstring, as `(table, column, comment)`."""


def upgrade() -> None:
    """Apply this revision."""
    for table, comment in TABLE_COMMENTS:
        op.create_table_comment(table, comment, existing_comment=None)
    for table, column, comment in COLUMN_COMMENTS:
        op.alter_column(table, column, comment=comment, existing_comment=None)


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    for table, column, _comment in reversed(COLUMN_COMMENTS):
        op.alter_column(table, column, comment=None)
    for table, _comment in reversed(TABLE_COMMENTS):
        op.drop_table_comment(table)
