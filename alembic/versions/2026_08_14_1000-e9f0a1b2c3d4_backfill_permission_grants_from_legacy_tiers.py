"""Backfill permission grants from the legacy tiers.

Every `global_administrators` row becomes an assignment of the `global-admin`
built-in role, and every `server_settings.trusted_roles_ids` entry becomes an
assignment of `trusted` to that Discord role. Assigning the built-in rather than
writing raw patterns keeps the migration honest: it is one row per subject, it
reads as "Trusted" in `/perm explain`, and it stays correct if the tier's meaning
is later refined in code.

The home-server extras (`build.submission.edit`, `build.submission.recalc`) are
deliberately **not** backfilled. This migration cannot read
`BotIdentityConfig.owner_server_id`, and granting cross-guild build-edit rights
to every guild's Trusted roles would be a real privilege escalation. Run these
two commands in the home guild after upgrading, and see `docs/rbac-cutover.md`, which
also records that `trusted` now carries `vote.weight.staff`:

    /perm grant @Trusted build.submission.edit   --scope global
    /perm grant @Trusted build.submission.recalc --scope global

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-08-14 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Value rewrite only: `api_keys.scopes` keeps its text-array shape, and its
# entries become node patterns matched against the same catalogue everything
# else resolves against.
_SCOPE_TO_NODE = {
    "builds:read": "build.submission.read",
    "builds:write": "build.submission.create",
    "verify": "account.verify.relay",
    "votes:cast": "vote.poll.cast",
    "users:read": "account.self.read",
}


def upgrade() -> None:
    """Assign built-in roles matching the legacy tiers, and rewrite key scopes."""
    # A grant issued by a migration or by the recovery CLI has no human grantor,
    # and inventing one would put a fictional account id into the audit trail.
    op.alter_column("permission_grants", "granted_by_account_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("permission_role_assignments", "granted_by_account_id", existing_type=sa.Integer(), nullable=True)

    op.execute(
        """
        INSERT INTO public.permission_role_assignments
            (role_id, subject_account_id, granted_by_account_id, granted_at, reason)
        SELECT
            roles.id,
            legacy.account_id,
            legacy.granted_by_account_id,
            legacy.granted_at,
            'Backfilled from global_administrators'
        FROM public.global_administrators AS legacy
        CROSS JOIN (SELECT id FROM public.permission_roles WHERE builtin_key = 'global-admin') AS roles
        ON CONFLICT DO NOTHING
        """
    )

    # scope_guild_id stays NULL rather than being pinned to the guild the role
    # lives in. The Trusted tier already reached global capabilities -- the
    # schematic diagnostics are cross-guild -- so scoping the assignment to the
    # guild would silently take those away, which is not what a backfill is for.
    op.execute(
        """
        INSERT INTO public.permission_role_assignments
            (role_id, subject_role_id, subject_guild_id, granted_by_account_id, reason)
        SELECT
            roles.id,
            trusted_role_id,
            settings.server_id,
            NULL,
            'Backfilled from server_settings.trusted_roles_ids'
        FROM public.server_settings AS settings
        CROSS JOIN LATERAL unnest(settings.trusted_roles_ids) AS trusted_role_id
        CROSS JOIN (SELECT id FROM public.permission_roles WHERE builtin_key = 'trusted') AS roles
        WHERE settings.trusted_roles_ids IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )

    for scope, node in _SCOPE_TO_NODE.items():
        op.execute(
            sa.text(
                """
                UPDATE public.api_keys
                SET scopes = array_replace(scopes, :scope, :node)
                WHERE :scope = ANY(scopes)
                """
            ).bindparams(scope=scope, node=node)
        )


def downgrade() -> None:
    """Remove the backfilled assignments and restore the legacy scope strings."""
    for scope, node in _SCOPE_TO_NODE.items():
        op.execute(
            sa.text(
                """
                UPDATE public.api_keys
                SET scopes = array_replace(scopes, :node, :scope)
                WHERE :node = ANY(scopes)
                """
            ).bindparams(scope=scope, node=node)
        )

    op.execute(
        """
        DELETE FROM public.permission_role_assignments
        WHERE reason IN (
            'Backfilled from global_administrators',
            'Backfilled from server_settings.trusted_roles_ids'
        )
        """
    )

    # Anything issued after the upgrade may legitimately have no grantor, so the
    # NOT NULL cannot simply be restored; drop those rows first.
    op.execute("DELETE FROM public.permission_grants WHERE granted_by_account_id IS NULL")
    op.execute("DELETE FROM public.permission_role_assignments WHERE granted_by_account_id IS NULL")
    op.alter_column("permission_grants", "granted_by_account_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("permission_role_assignments", "granted_by_account_id", existing_type=sa.Integer(), nullable=False)
