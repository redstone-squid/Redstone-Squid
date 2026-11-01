"""Permission-specific conflict policy inside the account merge transaction."""

from sqlalchemy import Delete, Update, and_, column, delete, func, select, table, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.selectable import ScalarSelect

from squid.permissions.infrastructure.models import PermissionGrant, PermissionRoleAssignment
from squid.persistence.account_merge import AccountMergeContext, AccountReference, move_account_references

PERMISSION_PROVENANCE_REFERENCES = (
    AccountReference("permission_roles", "created_by_account_id"),
    AccountReference("permission_role_patterns", "added_by_account_id"),
    AccountReference("permission_role_includes", "added_by_account_id"),
    AccountReference("permission_grants", "granted_by_account_id"),
    AccountReference("permission_role_assignments", "granted_by_account_id"),
    AccountReference("permission_audit_log", "actor_account_id"),
)


def _absorbed_permission_effect(context: AccountMergeContext) -> ScalarSelect[int]:
    absorbed = aliased(PermissionGrant, name="absorbed_grant")
    return (
        select(absorbed.effect)
        .where(
            absorbed.subject_account_id == context.absorbed,
            absorbed.pattern == PermissionGrant.pattern,
            absorbed.scope_guild_id.is_not_distinct_from(PermissionGrant.scope_guild_id),
        )
        .correlate(PermissionGrant)
        .scalar_subquery()
    )


def _merge_permission_effects_statement(context: AccountMergeContext) -> Update:
    absorbed_effect = _absorbed_permission_effect(context)
    return (
        update(PermissionGrant)
        .where(PermissionGrant.subject_account_id == context.survivor, absorbed_effect.is_not(None))
        .values(effect=func.least(PermissionGrant.effect, absorbed_effect))
    )


def _collapse_permission_grants_statement(context: AccountMergeContext) -> Delete:
    absorbed = aliased(PermissionGrant, name="absorbed_grant")
    survivor = aliased(PermissionGrant, name="survivor_grant")
    duplicate_ids = (
        select(absorbed.id)
        .join(
            survivor,
            and_(
                survivor.subject_account_id == context.survivor,
                survivor.pattern == absorbed.pattern,
                survivor.scope_guild_id.is_not_distinct_from(absorbed.scope_guild_id),
            ),
        )
        .where(absorbed.subject_account_id == context.absorbed)
    )
    return delete(PermissionGrant).where(PermissionGrant.id.in_(duplicate_ids))


def _collapse_permission_role_assignments_statement(context: AccountMergeContext) -> Delete:
    absorbed = aliased(PermissionRoleAssignment, name="absorbed_assignment")
    survivor = aliased(PermissionRoleAssignment, name="survivor_assignment")
    duplicate_ids = (
        select(absorbed.id)
        .join(
            survivor,
            and_(
                survivor.subject_account_id == context.survivor,
                survivor.role_id == absorbed.role_id,
                survivor.scope_guild_id.is_not_distinct_from(absorbed.scope_guild_id),
            ),
        )
        .where(absorbed.subject_account_id == context.absorbed)
    )
    return delete(PermissionRoleAssignment).where(PermissionRoleAssignment.id.in_(duplicate_ids))


async def merge_permissions(session: AsyncSession, context: AccountMergeContext) -> None:
    """Move permission provenance and collapse subject collisions to the restrictive effect."""
    await move_account_references(session, PERMISSION_PROVENANCE_REFERENCES, context)
    await session.execute(_merge_permission_effects_statement(context))
    await session.execute(_collapse_permission_grants_statement(context))
    await move_account_references(session, (AccountReference("permission_grants", "subject_account_id"),), context)
    await session.execute(_collapse_permission_role_assignments_statement(context))
    await move_account_references(
        session,
        (AccountReference("permission_role_assignments", "subject_account_id"),),
        context,
    )
    audit = table("permission_audit_log", column("subject_kind"), column("subject_id"))
    await session.execute(
        update(audit)
        .where(audit.c.subject_kind == "account", audit.c.subject_id == context.absorbed)
        .values(subject_id=context.survivor)
    )
