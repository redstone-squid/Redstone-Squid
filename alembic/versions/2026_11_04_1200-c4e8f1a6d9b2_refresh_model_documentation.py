"""Align database comments with the reviewed model documentation.

Revision ID: c4e8f1a6d9b2
Revises: b3d7f0a5c8e1
"""

from alembic import op

revision: str = "c4e8f1a6d9b2"
down_revision: str | None = "b3d7f0a5c8e1"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE_COMMENTS: dict[str, tuple[str | None, str]] = {
    "build_links": (
        "A link associated with a build (image, video, world download).",
        "A URL associated with a build, such as an image, schematic, or world download.",
    ),
    "build_schematics": (
        "One analyzed schematic attached to a build.\n"
        "\n"
        "Metrics and fingerprints are denormalised onto this row so duplicate "
        "shortlisting is a\n"
        "plain indexed query. Fingerprints are only comparable within the "
        "`analyzer_version` that\n"
        "produced them, so every identity index carries that column and every lookup "
        "filters on it;\n"
        "an engine upgrade therefore becomes a visible backfill rather than a silent "
        "regression.",
        "One analyzed file attached to a build, with its publication policy.\n"
        "\n"
        "Measurements and fingerprints are stored here for duplicate shortlisting. "
        "Fingerprint lookups\n"
        "include analyzer_version because an engine upgrade can change the hashes. "
        "Reanalyzing an\n"
        "attachment updates this evidence without changing its source bytes.",
    ),
    "build_vote_sessions": (None, "Link a build review to its target and recorded changes."),
    "delete_log_vote_sessions": (None, "Store the Discord message targeted by a deletion vote."),
    "discord_sync_queue": (
        "A coalesced request to refresh one Discord-rendered resource.\n"
        "\n"
        "Not an event queue, despite the shape. Rows are coalesced on\n"
        "`(resource_kind, source_key)` by the six `INSERT ... ON CONFLICT ... DO\n"
        "UPDATE` triggers in `squid/persistence/postgres_entities.sql` — that is "
        "where\n"
        "the coalescing actually happens — deleted on acknowledgement, and carry "
        "a\n"
        "`generation` compared against a post's applied revision as a staleness "
        "token\n"
        "rather than read as an ordering. An event log would be append-only and\n"
        "replayable; this table is neither. A row means \"this resource's Discord "
        "posts\n"
        'are stale", and re-reading it tells you what the resource should look '
        "like\n"
        "now, not what happened to it.\n"
        "\n"
        "Discord-specific on purpose: every row exists to repair a Discord post, "
        "and\n"
        "the only consumer renders them (`squid/bot/sync/reconciler.py`). The\n"
        "application layer speaks of *reconciliation* rather than *sync* because "
        "a\n"
        "sync names a transfer, and nothing here transfers anything.",
        "A pending refresh or deletion of a resource's Discord posts.\n"
        "\n"
        "Database triggers insert one row per resource and replace its action and\n"
        "generation when another change arrives. They also clear any existing "
        "claim,\n"
        "so a worker handling the older generation cannot acknowledge the new "
        "request.\n"
        "Workers read the resource's current state and delete the row on "
        "completion.\n"
        "\n"
        "The generation is compared with each post's applied revision. Several "
        "changes\n"
        "can share one pending row; this table does not retain their history.",
    ),
    "media_normalization_jobs": (
        "A retained, claim-token-fenced request to normalize one raw upload.",
        "A normalization request retained after completion, failure, or discard for cleanup and retries.",
    ),
    "notification_deliveries": (
        "A durable at-least-once Discord DM delivery attempt.",
        "A queued Discord DM retained across retries and after successful or terminal delivery.",
    ),
    "notifications": (
        "An idempotently materialized user notification.",
        "An event-derived message for one recipient, shared by the web inbox and DM delivery.",
    ),
    "permission_epoch": (
        "A single counter bumped by any permission write.\n"
        "\n"
        "Three processes each hold their own rule-set cache, so a grant issued in "
        "the\n"
        "API has to become visible in the bot. A trigger bumps this row and sends\n"
        "`NOTIFY squid_permissions`; watchers treat the notification as a latency "
        "hint\n"
        "and poll this counter as the durable signal.",
        "A shared counter used to invalidate permission caches.\n"
        "\n"
        "Database triggers increment the counter and send `NOTIFY "
        "squid_permissions`\n"
        "after permission changes. Watchers poll the counter and use notifications\n"
        "to refresh sooner.",
    ),
    "permission_role_patterns": (
        "One pattern a role includes or subtracts.\n"
        "\n"
        "Subtraction is not a deny: it withholds the pattern from *this* "
        "role's\n"
        "contribution, and any other role including it still confers it. "
        "That is\n"
        "Azure's `NotActions` semantics, and it is why a role can be written "
        'as "this\n'
        'namespace, minus its destructive members" without poisoning other '
        "grants.",
        "One included or excluded pattern in a role.\n"
        "\n"
        "Exclusions remove matching nodes from this role's contribution "
        "without denying\n"
        "those nodes when another role or direct rule grants them.",
    ),
    "permission_roles": (
        "A named bundle of permission patterns.\n"
        "\n"
        "Built-in roles keep their pattern lists in code, so this row exists only "
        "to\n"
        "give assignments a foreign key; any `permission_role_patterns` rows "
        "attached\n"
        "to a built-in are additive overrides on top of the code-defined list.",
        "A named bundle of permission patterns and management settings.\n"
        "\n"
        "Built-in pattern lists live in code. Stored patterns supplement those "
        "lists,\n"
        "while this row supplies the assignment target, rank, and display metadata.",
    ),
    "record_computation_runs": (
        "An immutable attempt to calculate records for one build and version scope.",
        "A computation attempt for one build kind and version, including its publication state.",
    ),
    "record_rulesets": (
        "An immutable version of the record calculators and title formatters.",
        "Calculator and formatter versions with a mutable activation timestamp.",
    ),
    "schematic_jobs": (
        "A durable request for the worker-owned native schematic engine.",
        "A durable native-engine request whose result remains available until "
        "retention expires.\n"
        "\n"
        "Claim tokens reject stale worker updates. Binary output objects are retained "
        "alongside the row\n"
        "for cleanup; input objects can be shared by multiple jobs.",
    ),
    "starboard_entries": (
        "The materialized-post state for one source message on one starboard.",
        "Current vote totals and rendering bookkeeping for one source message on one starboard.",
    ),
    "submission_draft_access": (
        "An account's role on a draft; v1 creates exactly one owner grant.",
        "An account's role on a draft; creation grants one account the owner role.",
    ),
    "submission_finalization_jobs": (
        "A retained source-draft job with UUID-fenced worker claims.",
        "A finalization attempt retained with its current worker claim and retry count.",
    ),
    "submission_inference_runs": (
        "Exact private inference inputs and retained candidate facts under a renewable claim.",
        "Private inference inputs, candidate results, and the current expiring worker claim.",
    ),
    "votes": (
        "A vote cast in a vote session.",
        "One account's selected option and cached positive weight in a session.",
    ),
}

_COLUMN_COMMENTS: dict[tuple[str, str], tuple[str | None, str]] = {
    ("build_schematics", "analysis_schema_version"): (
        "Which revision of *our* analysis produced this row, bumped when we change what we read.",
        "Application analysis format version, independent of the engine version.",
    ),
    ("build_schematics", "analyzer_version"): (
        "Which engine build produced the fingerprints on this row, e.g. `nucleation-0.9.2`.",
        "Engine version that produced these fingerprints; comparisons must use the same version.",
    ),
    ("build_schematics", "block_count"): (
        "Non-air block count. **Not** the Door Rules cumulative "
        "volume, which counts air pockets\n"
        "and carries hallway, frame, and hitbox exceptions no "
        "static read can apply.",
        "Non-air block count, separate from the Door Rules "
        "cumulative volume.\n"
        "\n"
        "The latter includes air pockets and needs hallway, frame, "
        "and hitbox review.",
    ),
    ("build_schematics", "file_sha256"): (
        "The stored bytes this analysis describes. `RESTRICT` "
        "because several builds can share\n"
        "one file and losing it would strand every analysis that "
        "references it.",
        "Digest of the analyzed bytes, which may be shared by "
        "several builds.\n"
        "\n"
        "Deletion is restricted while any attachment references the "
        "file.",
    ),
    ("build_schematics", "fingerprint_structural"): (
        "Coarse translation-invariant bucket. A build "
        "differing by a single block still matches,\n"
        "so this is a pre-filter feeding pairwise "
        "ranking and never a duplicate verdict by "
        "itself.",
        "Coarse translation-invariant hash used to "
        "shortlist files for pairwise comparison.\n"
        "\n"
        "Matching values can include different block "
        "arrangements and do not establish duplication.",
    ),
    ("build_schematics", "is_primary"): (
        "Whether this is the schematic shown on the build card and used for duplicate checks.",
        "Featured attachment used for generated previews, conversion, and timing measurements.",
    ),
    ("build_schematics", "original_filename"): (
        "The name the uploader's file had, kept for display only. Never trusted for typing.",
        "Original display filename; ingestion may also use its extension as a fallback format hint.",
    ),
    ("build_schematics", "uploaded_by_account_id"): (
        "Who supplied the file, beside "
        "`rights_attested_by_account_id` so the table\n"
        "carries one attribution style rather than two.",
        "Account that supplied the file, which can differ from the account attesting publication rights.",
    ),
    ("discord_sync_queue", "available_at"): (
        "When this row next becomes claimable, and the only "
        "column backoff writes.\n"
        "\n"
        "Kept separate from `enqueued_at` so a repeatedly "
        "failing row keeps its place in\n"
        "FIFO order and still reports its true age to the "
        "queue-health gauges.",
        "Earliest time a worker may claim this row.\n"
        "\n"
        "Retries move this time forward without changing "
        "enqueued_at, preserving queue\n"
        "order and the age reported by health metrics.",
    ),
    ("discord_sync_queue", "claim_token"): (
        "The database-minted fencing token handed to the worker "
        "that claimed this row.\n"
        "\n"
        "Nullable for now so code from the previous release, "
        "which stamps only\n"
        "`claimed_at`, can keep draining the queue across a "
        "deploy window.",
        "Token generated for the current claim.\n"
        "\n"
        "Completion and failure updates require a matching token. "
        "Enqueuing another\n"
        "update clears it, preventing an older worker from "
        "acknowledging the new work.",
    ),
    ("discord_sync_queue", "generation"): (
        "A globally monotonic staleness token, not a per-row "
        "counter.\n"
        "\n"
        "Acknowledging a job deletes its queue row, so a counter "
        "restarted at 1 on the next\n"
        "enqueue and could name a revision below one a post had "
        "already applied. Sequence\n"
        "values survive that because they are never rolled back or "
        "reused.",
        "Sequence number used to compare requested work with a "
        "post's applied revision.\n"
        "\n"
        "The sequence continues after a queue row is deleted, so "
        "the next request does\n"
        "not restart at a revision the post has already rendered.",
    ),
    ("error_reports", "context"): (
        "Redacted diagnostic context. Never contains stable Discord account identifiers.",
        "Diagnostic context supplied by the caller; the storage service does not redact it.",
    ),
    ("error_reports", "expires_at"): (
        "When retention drops this report.",
        "When the report stops appearing in lookups and becomes eligible for deletion.",
    ),
    ("error_reports", "message"): (
        "The exception's own string form, which is never shown to the user who triggered it.",
        "The exception's string form, available to callers authorized to read diagnostic details.",
    ),
    ("error_reports", "reference"): (
        "The shortened form shown to the user, indexed because it is "
        "what they quote back.\n"
        "\n"
        "Not unique: it is a 48-bit prefix of the correlation ID, so a "
        "collision is possible even\n"
        "though it is vanishingly unlikely, and a unique constraint "
        "would turn that into a failure to\n"
        "record the second error rather than an ambiguous lookup.",
        "Short reference shown to the user, usually the first 12 "
        "correlation-ID characters.\n"
        "\n"
        "References are not unique. Multiple failures can share a "
        "correlation ID, and\n"
        "different IDs can share a prefix; lookup also returns a match "
        "count.",
    ),
    ("generic_vote_sessions", "guild_id"): (
        "The guild whose emoji palette the poll was drafted "
        "against.\n"
        "\n"
        "Nullable so a poll can be created by a transport that "
        "has no guild -- the REST API\n"
        "or a standalone draft -- and have its presentation "
        "messages attached afterwards.",
        "Owning guild used for emoji configuration and ballot "
        "role weights.\n"
        "\n"
        "Guild polls may be created without one; network polls "
        "require an owner.\n"
        "Presentation messages are attached separately.",
    ),
    ("generic_vote_sessions", "scope"): (
        "Whether the poll is carded only in its own guild or in every vote channel.",
        "Whether publication is limited to the owning guild or spans configured vote channels.",
    ),
    ("idempotency_requests", "principal"): (
        "Rolling-deploy mirror removed after the caller-vocabulary compatibility window.",
        "Legacy copy of caller, still written by the repository for the retained database column.",
    ),
    ("messages", "deleted_at"): (
        "Set when Discord reports the message gone. The row is a retained fact, never erased.",
        "When Discord reported deletion. The deletion handler retains this row and its content.",
    ),
    ("permission_audit_log", "action"): (
        "What happened: `grant`, `revoke`, `role_create`, `role_pattern_add`, and so on.",
        "Mutation name, such as `grant`, `revoke`, `role_create`, or `role_include`.",
    ),
    ("permission_audit_log", "reason"): (
        "Mandatory for `forbid`, enforced by the service rather than "
        "the schema so\n"
        "the column stays usable for the actions that do not need "
        "it.",
        "Reason for the change; the service requires one for a forbid grant.",
    ),
    ("permission_role_assignments", "granted_by_account_id"): (
        "Who issued this, or NULL when the system did. See `PermissionGrant`.",
        "Account that issued the assignment, or None for a system operation.",
    ),
    ("permission_role_patterns", "mode"): (
        "1 to include, -1 to subtract. One mode per pattern, so a role cannot\ncontradict itself.",
        "1 to include or -1 to exclude; a role stores one mode for each pattern.",
    ),
    ("record_recompute_queue", "available_at"): (
        "When this row next becomes claimable, and the only column backoff writes.",
        "Earliest next claim time; retry backoff advances it without changing the enqueue time.",
    ),
    ("record_recompute_queue", "claim_token"): (
        "The database-minted fencing token handed to the "
        "worker that leased this scope.\n"
        "\n"
        "Scopes are leased in batches and acknowledged as a "
        "set, so the token is what\n"
        "tells a finishing worker's acknowledgement from work "
        "enqueued during its run.",
        "Token identifying the current claim, cleared when "
        "new work replaces it.\n"
        "\n"
        "Completion and failure updates match this token to "
        "preserve work enqueued\n"
        "or reclaimed after the worker started.",
    ),
    ("schematic_jobs", "claim_token"): (
        "The database-minted fencing token handed to the worker that claimed this row.",
        "Token that must still match when the claiming worker records its outcome.",
    ),
    ("schematic_preview_objects", "cleanup_claim_token"): (
        "Fence shared by cleanup acknowledgement and publication checks.",
        "Token required to finish cleanup; its presence prevents reuse or publication.",
    ),
    ("schematic_render_queue", "available_at"): (
        "When this row next becomes claimable, and the only column backoff writes.",
        "Earliest time a worker may claim this job, including retry backoff.",
    ),
    ("schematic_render_queue", "claim_token"): (
        "The database-minted fencing token handed to the worker that claimed this row.",
        "Token that must still match when the claiming worker records its outcome.",
    ),
    ("schematic_renders", "recipe_hash"): (
        "SHA-256 of the pack, camera recipe, output dimensions, and analyzer version.",
        "Hash of the source and pack digests, framing, schematic dimensions, and analyzer version.",
    ),
    ("search_embedding_queue", "available_at"): (
        "When this row next becomes claimable, and the only column backoff writes.",
        "Earliest time a worker may claim this item; retries move it into the future.",
    ),
    ("search_embedding_queue", "claim_token"): (
        "The database-minted fencing token handed to the worker that claimed this row.",
        "Token generated for the current claim; stale workers cannot acknowledge a newer claim.",
    ),
    ("search_projection_queue", "available_at"): (
        "When this row next becomes claimable, and the only column backoff writes.",
        "Earliest time a worker may claim this item; retries move it into the future.",
    ),
    ("search_projection_queue", "claim_token"): (
        "The database-minted fencing token handed to the worker that claimed this row.",
        "Token generated for the current claim; stale workers cannot acknowledge a newer claim.",
    ),
    ("starboard_sources", "channel_id"): (
        None,
        "Source channel ID, or zero to include every channel in the source guild.",
    ),
    ("starboards", "allow_bots"): (
        None,
        "Whether messages authored by bots may qualify; this does not authorize bot voters.",
    ),
    ("starboards", "max_age_seconds"): (
        None,
        "Maximum source-message age in seconds; zero disables the upper age limit.",
    ),
    ("starboards", "required"): (None, "Score at or above which an unposted entry qualifies for publication."),
    ("starboards", "required_remove"): (
        None,
        "Score at or below which an existing entry no longer qualifies for publication.",
    ),
    ("versions", "data_version"): (
        "Minecraft's own numeric world-format version, needed to retarget "
        "a schematic at this\n"
        "release. Nullable because Bedrock has no equivalent and Java "
        "releases predating the field\n"
        "have none either.",
        "Minecraft's world-format number, used when retargeting "
        "schematics.\n"
        "\n"
        "None means no data version is recorded. Bedrock and early Java "
        "releases do not\n"
        "have one.",
    ),
    ("vote_session_options", "guild_id"): (
        None,
        "Guild using this emoji alias, or 0 for an option shared across guilds.",
    ),
    ("vote_sessions", "fail_threshold"): (None, "Negative net score required for denial, or None for a generic poll."),
    ("vote_sessions", "pass_threshold"): (
        None,
        "Positive net score required for approval, or None for a generic poll.",
    ),
    ("vote_sessions", "result"): (
        "The result of the vote session.",
        "Pending until closure; threshold outcomes are approved or denied, and other closures are cancelled.",
    ),
    ("votes", "weight"): (
        None,
        "Cached positive magnitude; the option choice determines the sign used in threshold scoring.",
    ),
}


def upgrade() -> None:
    """Update comments without changing stored data or table structure."""
    for table, (old, new) in _TABLE_COMMENTS.items():
        op.create_table_comment(table, new, existing_comment=old)
    for (table, column), (old, new) in _COLUMN_COMMENTS.items():
        op.alter_column(table, column, comment=new, existing_comment=old)


def downgrade() -> None:
    """Restore the comments present before the documentation review."""
    for (table, column), (old, new) in reversed(_COLUMN_COMMENTS.items()):
        op.alter_column(table, column, comment=old, existing_comment=new)
    for table, (old, new) in reversed(_TABLE_COMMENTS.items()):
        if old is None:
            op.drop_table_comment(table, existing_comment=new)
        else:
            op.create_table_comment(table, old, existing_comment=new)
