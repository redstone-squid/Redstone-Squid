"""Coordinate feature-owned account merge phases in one caller-owned transaction."""

from sqlalchemy import Delete, and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from squid.accounts.domain import AccountMerge, ClaimStatus
from squid.accounts.errors import AccountNotFoundError
from squid.accounts.infrastructure.models import Account as AccountModel
from squid.accounts.infrastructure.models import CreatorAliasClaim as CreatorAliasClaimModel
from squid.accounts.infrastructure.models import PublicCreatorRedirect
from squid.notifications.infrastructure.account_merge import merge_account_notifications
from squid.permissions.infrastructure.account_merge import merge_permissions
from squid.persistence.account_merge import AccountMergeContext, AccountReference, move_account_references
from squid.submissions.infrastructure.account_merge import merge_submissions
from squid.voting.infrastructure.account_merge import merge_voting

IDENTITY_PROFILE_REFERENCES = (
    AccountReference("minecraft_paper_installations", "owner_account_id"),
    AccountReference("minecraft_player_challenges", "approved_by_account_id"),
    AccountReference("minecraft_player_grants", "account_id"),
    AccountReference("api_keys", "owner_account_id"),
    AccountReference("api_keys", "created_by_account_id"),
    AccountReference("web_sessions", "account_id"),
    AccountReference("cli_device_enrollments", "approved_by_account_id"),
    AccountReference("cli_devices", "account_id"),
    AccountReference("account_identities", "account_id"),
)


CREATOR_CREDIT_REFERENCES = (
    AccountReference("creator_aliases", "account_id"),
    AccountReference("creator_alias_claims", "resolved_by_account_id"),
)


def _collapse_alias_claims_statement(context: AccountMergeContext) -> Delete:
    absorbed = aliased(CreatorAliasClaimModel, name="absorbed_claim")
    survivor = aliased(CreatorAliasClaimModel, name="survivor_claim")
    duplicate_ids = (
        select(absorbed.id)
        .join(
            survivor,
            and_(
                survivor.account_id == context.survivor,
                survivor.alias_id == absorbed.alias_id,
                survivor.status == ClaimStatus.PENDING,
            ),
        )
        .where(absorbed.account_id == context.absorbed, absorbed.status == ClaimStatus.PENDING)
    )
    return delete(CreatorAliasClaimModel).where(CreatorAliasClaimModel.id.in_(duplicate_ids))


async def _merge_creator_credit(session: AsyncSession, context: AccountMergeContext) -> None:
    """Move creator aliases and collapse duplicate pending claims."""
    await move_account_references(session, CREATOR_CREDIT_REFERENCES, context)
    await session.execute(_collapse_alias_claims_statement(context))
    await move_account_references(session, (AccountReference("creator_alias_claims", "account_id"),), context)


async def _retire_absorbed_account(
    session: AsyncSession,
    survivor: AccountModel,
    absorbed: AccountModel,
) -> None:
    """Finish the merge by preserving public redirects and deleting the source row."""
    await session.execute(
        update(PublicCreatorRedirect)
        .where(PublicCreatorRedirect.target_account_id == absorbed.id)
        .values(target_account_id=survivor.id)
    )
    session.add(
        PublicCreatorRedirect(
            retired_public_creator_id=absorbed.public_creator_id,
            target_account_id=survivor.id,
        )
    )
    await session.delete(absorbed)


async def merge_accounts(session: AsyncSession, context: AccountMergeContext) -> AccountMerge:
    """Lock both accounts in ID order and apply every phase before retiring the absorbed row."""
    locked = (
        await session.scalars(
            select(AccountModel)
            .where(AccountModel.id.in_((context.survivor, context.absorbed)))
            .order_by(AccountModel.id)
            .with_for_update()
        )
    ).all()
    by_id = {account.id: account for account in locked}
    survivor = by_id.get(context.survivor)
    absorbed = by_id.get(context.absorbed)
    if survivor is None:
        raise AccountNotFoundError(context.survivor)
    if absorbed is None:
        raise AccountNotFoundError(context.absorbed)

    await merge_submissions(session, context)
    await move_account_references(session, IDENTITY_PROFILE_REFERENCES, context)
    await _merge_creator_credit(session, context)
    await merge_voting(session, context)
    await merge_account_notifications(session, survivor=context.survivor, absorbed=context.absorbed)
    await merge_permissions(session, context)
    result = AccountMerge(
        surviving_account_id=context.survivor,
        absorbed_account_id=context.absorbed,
        surviving_public_creator_id=survivor.public_creator_id,
        redirected_public_creator_id=absorbed.public_creator_id,
    )
    await _retire_absorbed_account(session, survivor, absorbed)
    return result
