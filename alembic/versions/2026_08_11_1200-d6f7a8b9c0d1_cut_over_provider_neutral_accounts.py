"""cut over provider neutral accounts

Revision ID: d6f7a8b9c0d1
Revises: c5e6f7a8b9c0
Create Date: 2026-08-11 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "c5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace provider-shaped users with accounts and verified identities."""
    op.rename_table("users", "accounts")
    op.execute("ALTER SEQUENCE IF EXISTS users_id_seq RENAME TO accounts_id_seq")
    _rename_constraint("accounts", "users_pkey", "accounts_pkey")
    _rename_constraint("accounts", "users_public_creator_id_key", "accounts_public_creator_id_key")
    _rename_constraint("accounts", "users_consent_receipt_complete", "accounts_consent_receipt_complete")
    op.create_table_comment(
        "accounts",
        "An internal principal independent of every external identity provider.",
        existing_comment="An account we hold a relationship with, linking Discord and Minecraft identities.",
    )

    op.create_table(
        "account_identities",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "provider IN ('discord', 'java', 'bedrock')",
            name="account_identities_provider_check",
        ),
        sa.CheckConstraint(
            "subject = btrim(subject) AND subject <> ''",
            name="account_identities_subject_check",
        ),
        sa.CheckConstraint(
            "(provider <> 'discord' OR subject ~ '^[1-9][0-9]*$') AND "
            "(provider <> 'bedrock' OR subject ~ '^[1-9][0-9]*$') AND "
            "(provider <> 'java' OR subject ~ "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')",
            name="account_identities_subject_format_check",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="account_identities_account_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "subject", name="account_identities_provider_subject_key"),
        comment="A verified provider subject attached to exactly one account.",
    )
    op.create_index(
        "account_identities_account_provider_idx",
        "account_identities",
        ["account_id", "provider"],
    )
    op.execute(
        """
        INSERT INTO account_identities (account_id, provider, subject, verified_at, created_at)
        SELECT id, 'discord', discord_id::text, COALESCE(created_at, now()), COALESCE(created_at, now())
        FROM accounts WHERE discord_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO account_identities
            (account_id, provider, subject, display_name, verified_at, created_at)
        SELECT id, 'java', minecraft_uuid::text, ign,
               COALESCE(consented_at, created_at, now()), COALESCE(created_at, now())
        FROM accounts WHERE minecraft_uuid IS NOT NULL
        """
    )
    # Votes, grant audits, and resolved claims historically held Discord IDs without
    # corresponding users rows. Give each one an account without inferring any merge.
    op.execute(
        """
        DO $$
        DECLARE candidate record;
        DECLARE created_account_id integer;
        BEGIN
            FOR candidate IN
                SELECT DISTINCT subject FROM (
                    SELECT resolved_by_discord_id::text AS subject
                    FROM creator_alias_claims WHERE resolved_by_discord_id IS NOT NULL
                    UNION SELECT discord_id::text FROM global_administrators
                    UNION SELECT granted_by_discord_id::text FROM global_administrators
                    UNION SELECT author_id::text FROM vote_sessions
                    UNION SELECT user_id::text FROM votes
                ) subjects
            LOOP
                IF NOT EXISTS (
                    SELECT 1 FROM account_identities
                    WHERE provider = 'discord' AND subject = candidate.subject
                ) THEN
                    INSERT INTO accounts DEFAULT VALUES RETURNING id INTO created_account_id;
                    INSERT INTO account_identities (account_id, provider, subject)
                    VALUES (created_account_id, 'discord', candidate.subject);
                END IF;
            END LOOP;
        END;
        $$
        """
    )

    op.create_table(
        "public_creator_redirects",
        sa.Column("retired_public_creator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_account_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_account_id"],
            ["accounts.id"],
            name="public_creator_redirects_target_account_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("retired_public_creator_id"),
        comment="Permanent redirect from a merged public creator identifier.",
    )

    op.create_table_comment(
        "creator_alias_claims",
        "An account's request to be credited under a creator alias.",
        existing_comment="A user's request to be credited under a creator alias, pending staff review.",
    )
    op.create_table_comment(
        "verification_codes",
        "A verification code for linking Java Edition identities.",
        existing_comment="A verification code for linking Minecraft accounts.",
    )

    _rename_internal_account_column(
        "creator_aliases", "user_id", "account_id", "creator_aliases_user_id_fkey", "creator_aliases_account_id_fkey"
    )
    _rename_internal_account_column(
        "creator_alias_claims",
        "user_id",
        "account_id",
        "creator_alias_claims_user_id_fkey",
        "creator_alias_claims_account_id_fkey",
    )
    op.execute(
        "ALTER INDEX creator_alias_claims_one_pending_per_user RENAME TO creator_alias_claims_one_pending_per_account"
    )

    op.add_column("creator_alias_claims", sa.Column("resolved_by_account_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE creator_alias_claims claim
        SET resolved_by_account_id = identity.account_id
        FROM account_identities identity
        WHERE identity.provider = 'discord'
          AND identity.subject = claim.resolved_by_discord_id::text
        """
    )
    op.create_foreign_key(
        "creator_alias_claims_resolved_by_account_id_fkey",
        "creator_alias_claims",
        "accounts",
        ["resolved_by_account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_column("creator_alias_claims", "resolved_by_discord_id")

    _rename_internal_account_column(
        "builds",
        "submitter_user_id",
        "submitter_account_id",
        "builds_submitter_user_id_fkey",
        "builds_submitter_account_id_fkey",
    )
    _replace_emit_domain_event("submitter_account_id", submitted_schema=2, moderation_schema=3)
    _rename_internal_account_column(
        "web_sessions", "user_id", "account_id", "web_sessions_user_id_fkey", "web_sessions_account_id_fkey"
    )
    op.add_column("web_sessions", sa.Column("discord_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE web_sessions web_session
        SET discord_id = identity.subject::bigint
        FROM account_identities identity
        WHERE identity.account_id = web_session.account_id AND identity.provider = 'discord'
        """
    )
    op.alter_column("web_sessions", "discord_id", nullable=False)
    _rename_internal_account_column(
        "api_keys",
        "owner_user_id",
        "owner_account_id",
        "api_keys_owner_user_id_fkey",
        "api_keys_owner_account_id_fkey",
    )
    op.alter_column("api_keys", "created_by", new_column_name="created_by_account_id")
    _rename_constraint("api_keys", "api_keys_created_by_fkey", "api_keys_created_by_account_id_fkey")

    for table, old_constraint, new_constraint in (
        ("notification_profiles", "notification_profiles_user_id_fkey", "notification_profiles_account_id_fkey"),
        (
            "notification_subscriptions",
            "notification_subscriptions_user_id_fkey",
            "notification_subscriptions_account_id_fkey",
        ),
        ("notifications", "notifications_user_id_fkey", "notifications_account_id_fkey"),
        (
            "notification_deliveries",
            "notification_deliveries_user_id_fkey",
            "notification_deliveries_account_id_fkey",
        ),
    ):
        _rename_internal_account_column(table, "user_id", "account_id", old_constraint, new_constraint)
    op.execute("ALTER INDEX notification_subscriptions_user_idx RENAME TO notification_subscriptions_account_idx")
    op.execute("ALTER INDEX notifications_user_inbox_idx RENAME TO notifications_account_inbox_idx")

    _cut_over_global_administrators()
    _cut_over_votes()

    op.drop_constraint("users_minecraft_link_requires_consent", "accounts", type_="check")
    op.drop_constraint("users_minecraft_uuid_key", "accounts", type_="unique")
    op.drop_constraint("users_discord_id_key", "accounts", type_="unique")
    op.drop_column("accounts", "ign")
    op.drop_column("accounts", "minecraft_uuid")
    op.drop_column("accounts", "discord_id")


def downgrade() -> None:
    """Restore the provider-shaped schema when no post-cutover features were used."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public_creator_redirects) THEN
                RAISE EXCEPTION 'cannot downgrade after account merges';
            END IF;
            IF EXISTS (SELECT 1 FROM account_identities WHERE provider = 'bedrock') THEN
                RAISE EXCEPTION 'cannot downgrade after Bedrock identities are linked';
            END IF;
            IF EXISTS (
                SELECT 1 FROM account_identities GROUP BY account_id, provider HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'cannot downgrade accounts with multiple identities from one provider';
            END IF;
        END;
        $$
        """
    )
    op.add_column("accounts", sa.Column("discord_id", sa.BigInteger(), nullable=True))
    op.add_column("accounts", sa.Column("minecraft_uuid", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("accounts", sa.Column("ign", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE accounts account
        SET discord_id = identity.subject::bigint
        FROM account_identities identity
        WHERE identity.account_id = account.id AND identity.provider = 'discord'
        """
    )
    op.execute(
        """
        UPDATE accounts account
        SET minecraft_uuid = identity.subject::uuid, ign = identity.display_name
        FROM account_identities identity
        WHERE identity.account_id = account.id AND identity.provider = 'java'
        """
    )
    op.create_unique_constraint("users_discord_id_key", "accounts", ["discord_id"])
    op.create_unique_constraint("users_minecraft_uuid_key", "accounts", ["minecraft_uuid"])
    op.create_check_constraint(
        "users_minecraft_link_requires_consent",
        "accounts",
        "minecraft_uuid IS NULL OR consent_version IS NOT NULL OR created_at < TIMESTAMPTZ '2026-08-04T00:00:00+00:00'",
    )

    _restore_votes()
    _restore_global_administrators()

    op.execute("ALTER INDEX notifications_account_inbox_idx RENAME TO notifications_user_inbox_idx")
    op.execute("ALTER INDEX notification_subscriptions_account_idx RENAME TO notification_subscriptions_user_idx")
    for table, old_constraint, new_constraint in (
        (
            "notification_deliveries",
            "notification_deliveries_account_id_fkey",
            "notification_deliveries_user_id_fkey",
        ),
        ("notifications", "notifications_account_id_fkey", "notifications_user_id_fkey"),
        (
            "notification_subscriptions",
            "notification_subscriptions_account_id_fkey",
            "notification_subscriptions_user_id_fkey",
        ),
        ("notification_profiles", "notification_profiles_account_id_fkey", "notification_profiles_user_id_fkey"),
    ):
        _rename_internal_account_column(table, "account_id", "user_id", old_constraint, new_constraint)

    op.alter_column("api_keys", "created_by_account_id", new_column_name="created_by")
    _rename_constraint("api_keys", "api_keys_created_by_account_id_fkey", "api_keys_created_by_fkey")
    _rename_internal_account_column(
        "api_keys",
        "owner_account_id",
        "owner_user_id",
        "api_keys_owner_account_id_fkey",
        "api_keys_owner_user_id_fkey",
    )
    op.drop_column("web_sessions", "discord_id")
    _rename_internal_account_column(
        "web_sessions", "account_id", "user_id", "web_sessions_account_id_fkey", "web_sessions_user_id_fkey"
    )
    _rename_internal_account_column(
        "builds",
        "submitter_account_id",
        "submitter_user_id",
        "builds_submitter_account_id_fkey",
        "builds_submitter_user_id_fkey",
    )
    _replace_emit_domain_event("submitter_user_id", submitted_schema=1, moderation_schema=2)

    op.add_column("creator_alias_claims", sa.Column("resolved_by_discord_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE creator_alias_claims claim
        SET resolved_by_discord_id = identity.subject::bigint
        FROM account_identities identity
        WHERE identity.account_id = claim.resolved_by_account_id AND identity.provider = 'discord'
        """
    )
    op.drop_constraint("creator_alias_claims_resolved_by_account_id_fkey", "creator_alias_claims", type_="foreignkey")
    op.drop_column("creator_alias_claims", "resolved_by_account_id")
    op.execute(
        "ALTER INDEX creator_alias_claims_one_pending_per_account RENAME TO creator_alias_claims_one_pending_per_user"
    )
    _rename_internal_account_column(
        "creator_alias_claims",
        "account_id",
        "user_id",
        "creator_alias_claims_account_id_fkey",
        "creator_alias_claims_user_id_fkey",
    )
    _rename_internal_account_column(
        "creator_aliases",
        "account_id",
        "user_id",
        "creator_aliases_account_id_fkey",
        "creator_aliases_user_id_fkey",
    )

    op.drop_table("public_creator_redirects")
    op.drop_index("account_identities_account_provider_idx", table_name="account_identities")
    op.drop_table("account_identities")
    op.create_table_comment(
        "verification_codes",
        "A verification code for linking Minecraft accounts.",
        existing_comment="A verification code for linking Java Edition identities.",
    )
    op.create_table_comment(
        "creator_alias_claims",
        "A user's request to be credited under a creator alias, pending staff review.",
        existing_comment="An account's request to be credited under a creator alias.",
    )
    op.create_table_comment(
        "accounts",
        "An account we hold a relationship with, linking Discord and Minecraft identities.",
        existing_comment="An internal principal independent of every external identity provider.",
    )
    _rename_constraint("accounts", "accounts_consent_receipt_complete", "users_consent_receipt_complete")
    _rename_constraint("accounts", "accounts_public_creator_id_key", "users_public_creator_id_key")
    _rename_constraint("accounts", "accounts_pkey", "users_pkey")
    op.rename_table("accounts", "users")
    op.execute("ALTER SEQUENCE IF EXISTS accounts_id_seq RENAME TO users_id_seq")


def _cut_over_global_administrators() -> None:
    op.add_column("global_administrators", sa.Column("account_id", sa.Integer(), nullable=True))
    op.add_column("global_administrators", sa.Column("granted_by_account_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE global_administrators grant_row
        SET account_id = recipient.account_id,
            granted_by_account_id = grantor.account_id
        FROM account_identities recipient, account_identities grantor
        WHERE recipient.provider = 'discord' AND recipient.subject = grant_row.discord_id::text
          AND grantor.provider = 'discord' AND grantor.subject = grant_row.granted_by_discord_id::text
        """
    )
    op.drop_constraint("global_administrators_pkey", "global_administrators", type_="primary")
    op.drop_column("global_administrators", "granted_by_discord_id")
    op.drop_column("global_administrators", "discord_id")
    op.alter_column("global_administrators", "account_id", nullable=False)
    op.alter_column("global_administrators", "granted_by_account_id", nullable=False)
    op.create_primary_key("global_administrators_pkey", "global_administrators", ["account_id"])
    op.create_foreign_key(
        "global_administrators_account_id_fkey",
        "global_administrators",
        "accounts",
        ["account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "global_administrators_granted_by_account_id_fkey",
        "global_administrators",
        "accounts",
        ["granted_by_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def _restore_global_administrators() -> None:
    op.add_column("global_administrators", sa.Column("discord_id", sa.BigInteger(), nullable=True))
    op.add_column("global_administrators", sa.Column("granted_by_discord_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE global_administrators grant_row
        SET discord_id = recipient.subject::bigint,
            granted_by_discord_id = grantor.subject::bigint
        FROM account_identities recipient, account_identities grantor
        WHERE recipient.provider = 'discord' AND recipient.account_id = grant_row.account_id
          AND grantor.provider = 'discord' AND grantor.account_id = grant_row.granted_by_account_id
        """
    )
    op.alter_column("global_administrators", "discord_id", nullable=False)
    op.alter_column("global_administrators", "granted_by_discord_id", nullable=False)
    op.drop_constraint("global_administrators_granted_by_account_id_fkey", "global_administrators", type_="foreignkey")
    op.drop_constraint("global_administrators_account_id_fkey", "global_administrators", type_="foreignkey")
    op.drop_constraint("global_administrators_pkey", "global_administrators", type_="primary")
    op.drop_column("global_administrators", "granted_by_account_id")
    op.drop_column("global_administrators", "account_id")
    op.create_primary_key("global_administrators_pkey", "global_administrators", ["discord_id"])


def _cut_over_votes() -> None:
    op.add_column("vote_sessions", sa.Column("author_account_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE vote_sessions vote_session
        SET author_account_id = identity.account_id
        FROM account_identities identity
        WHERE identity.provider = 'discord' AND identity.subject = vote_session.author_id::text
        """
    )
    op.alter_column("vote_sessions", "author_account_id", nullable=False)
    op.create_foreign_key(
        "vote_sessions_author_account_id_fkey",
        "vote_sessions",
        "accounts",
        ["author_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_column("vote_sessions", "author_id")

    op.add_column("votes", sa.Column("account_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE votes vote
        SET account_id = identity.account_id
        FROM account_identities identity
        WHERE identity.provider = 'discord' AND identity.subject = vote.user_id::text
        """
    )
    op.drop_constraint("votes_pkey", "votes", type_="primary")
    op.alter_column("votes", "user_id", new_column_name="discord_id")
    op.alter_column("votes", "account_id", nullable=False)
    op.create_primary_key("votes_pkey", "votes", ["vote_session_id", "account_id"])
    op.create_foreign_key("votes_account_id_fkey", "votes", "accounts", ["account_id"], ["id"], ondelete="CASCADE")


def _restore_votes() -> None:
    op.add_column("vote_sessions", sa.Column("author_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE vote_sessions vote_session
        SET author_id = identity.subject::bigint
        FROM account_identities identity
        WHERE identity.provider = 'discord' AND identity.account_id = vote_session.author_account_id
        """
    )
    op.alter_column("vote_sessions", "author_id", nullable=False)
    op.drop_constraint("vote_sessions_author_account_id_fkey", "vote_sessions", type_="foreignkey")
    op.drop_column("vote_sessions", "author_account_id")

    op.drop_constraint("votes_account_id_fkey", "votes", type_="foreignkey")
    op.drop_constraint("votes_pkey", "votes", type_="primary")
    op.alter_column("votes", "discord_id", new_column_name="user_id")
    op.drop_column("votes", "account_id")
    op.create_primary_key("votes_pkey", "votes", ["vote_session_id", "user_id"])


def _rename_internal_account_column(
    table: str,
    old_column: str,
    new_column: str,
    old_constraint: str,
    new_constraint: str,
) -> None:
    op.alter_column(table, old_column, new_column_name=new_column)
    _rename_constraint(table, old_constraint, new_constraint)


def _rename_constraint(table: str, old_name: str, new_name: str) -> None:
    op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old_name}" TO "{new_name}"')


def _replace_emit_domain_event(
    submitter_column: str,
    *,
    submitted_schema: int,
    moderation_schema: int,
) -> None:
    """Keep the build event envelope aligned with the renamed ownership column."""
    if submitter_column not in {"submitter_account_id", "submitter_user_id"}:
        raise ValueError(submitter_column)
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.emit_domain_event() RETURNS trigger
            LANGUAGE plpgsql
            AS $$
        DECLARE
            target_type text;
            target_kind text;
            target_id bigint;
            target_payload jsonb;
            target_schema_version integer := 1;
        BEGIN
            -- Unlike discord_sync_queue, this log records transitions, so an UPDATE that
            -- rewrites a column to the value it already held must not produce an event.
            IF TG_TABLE_NAME = 'builds' THEN
                IF TG_OP = 'INSERT' THEN
                    target_kind := 'build';
                    target_id := NEW.id;
                    target_type := 'build.submitted';
                    target_schema_version := {submitted_schema};
                    target_payload := jsonb_build_object(
                        'status', NEW.submission_status,
                        '{submitter_column}', NEW.{submitter_column},
                        'category', NEW.category
                    );
                    PERFORM public.publish_domain_event(
                        target_type, target_schema_version, target_kind, target_id, target_payload
                    );
                    RETURN NULL;
                END IF;
                IF OLD.submission_status IS NOT DISTINCT FROM NEW.submission_status THEN RETURN NULL; END IF;
                target_kind := 'build';
                target_id := NEW.id;
                target_schema_version := {moderation_schema};
                IF NEW.submission_status = 1 THEN
                    target_type := 'build.confirmed';
                ELSIF NEW.submission_status = 2 THEN
                    target_type := 'build.denied';
                ELSE
                    RETURN NULL;
                END IF;
                target_payload := jsonb_build_object(
                    'previous_status', OLD.submission_status,
                    'status', NEW.submission_status,
                    '{submitter_column}', NEW.{submitter_column},
                    'category', NEW.category,
                    'first_confirmation', NEW.submission_status = 1 AND NOT EXISTS (
                        SELECT 1
                        FROM public.domain_events
                        WHERE aggregate_kind = 'build'
                          AND aggregate_id = NEW.id
                          AND event_type = 'build.confirmed'
                    )
                );
            ELSE
                IF OLD.status IS NOT DISTINCT FROM NEW.status OR NEW.status <> 'closed' THEN RETURN NULL; END IF;
                target_kind := 'vote_session';
                target_id := NEW.id;
                target_type := 'vote_session.closed';
                target_payload := jsonb_build_object('kind', NEW.kind, 'result', NEW.result);
            END IF;

            PERFORM public.publish_domain_event(
                target_type, target_schema_version, target_kind, target_id, target_payload
            );

            RETURN NULL;
        END;
        $$;
        """
    )
