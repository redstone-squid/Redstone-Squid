"""Turn an account into a tombstone in one transaction.

The account row stays so that builds, ballots, reviews, and staff actions keep a
valid reference, but nothing on it or linked to it identifies the person any more.
Private state is erased, the account's build credits follow the owner's
CreditRetention choice, and unsubmitted drafts are discarded. Deletion is refused
while the account has submissions under review or votes it opened that are still
open, because those would be left without anyone to answer for them.

The policy for every reference to an account is in docs/account-deletion.md, and
tests/architecture/test_account_deletion.py keeps it in step with the schema. The
caller opens a build editing scope, because credit changes write build history,
and owns commit or rollback.
"""

from sqlalchemy import column, delete, select, table, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.core.accounts.domain import CreditRetention
from squid.core.accounts.errors import AccountDeletionBlockedError, AccountNotFoundError
from squid.core.accounts.models import Account, CreatorAlias
from squid.core.persistence.account_merge import AccountReference
from squid.core.submissions.infrastructure.account_deletion import (
    count_unsettled_submissions,
    erase_unsubmitted_drafts,
)
from squid.core.voting.infrastructure.account_deletion import count_open_vote_sessions

ERASED_REFERENCES = (
    AccountReference("account_profiles", "account_id"),
    AccountReference("account_identities", "account_id"),
    AccountReference("account_merge_tickets", "account_id"),
    AccountReference("catalogue_preferences", "account_id"),
    AccountReference("creator_alias_claims", "account_id"),
    AccountReference("web_sessions", "account_id"),
    AccountReference("api_keys", "owner_account_id"),
    AccountReference("cli_devices", "account_id"),
    AccountReference("cli_device_enrollments", "approved_by_account_id"),
    # Deleting an installation also ends the player grants issued through that server.
    AccountReference("minecraft_paper_installations", "owner_account_id"),
    AccountReference("minecraft_player_challenges", "approved_by_account_id"),
    AccountReference("minecraft_player_grants", "account_id"),
    AccountReference("notification_deliveries", "account_id"),
    AccountReference("notifications", "account_id"),
    AccountReference("notification_subscriptions", "account_id"),
    AccountReference("notification_profiles", "account_id"),
    AccountReference("permission_grants", "subject_account_id"),
    AccountReference("permission_role_assignments", "subject_account_id"),
    AccountReference("voter_guild_members", "account_id"),
    AccountReference("submission_draft_editors", "account_id"),
)
"""Rows that exist only for the account and are deleted outright."""

# A credit names the account through an explicit link or a matching provider identity,
# so it must be resolved before the identities are erased.
_CREDITED = "SELECT build_id, position FROM build_credit_accounts() WHERE account_id = :account"

_LOCK_CREDITED_BUILDS = f"""
    SELECT id FROM builds WHERE id IN (
        SELECT build_id FROM ({_CREDITED}) AS credited
        UNION SELECT build_id FROM build_credits WHERE proposed_account_id = :account
    ) ORDER BY id FOR UPDATE
"""

# Without a provider subject a credit can no longer match an identity, including
# one the same person links to a new account later.
_KEEP_NAMES = f"""
    UPDATE build_credits AS credit
    SET kind = 'name', subject = NULL, username = NULL, account_id = NULL, account_source = 'automatic'
    FROM ({_CREDITED}) AS credited
    WHERE credit.build_id = credited.build_id AND credit.position = credited.position
"""

_REMOVE_CREDITS = f"""
    DELETE FROM build_credits AS credit
    USING ({_CREDITED}) AS credited
    WHERE credit.build_id = credited.build_id AND credit.position = credited.position
    RETURNING credit.build_id
"""

# Close the gaps left by removed credits. The position key is deferred until commit.
_RENUMBER_CREDITS = """
    UPDATE build_credits AS credit SET position = ordered.position
    FROM (
        SELECT id, (row_number() OVER (PARTITION BY build_id ORDER BY position) - 1)::smallint AS position
        FROM build_credits WHERE build_id = ANY(:builds)
    ) AS ordered
    WHERE credit.id = ordered.id AND credit.position <> ordered.position
"""


async def delete_account(session: AsyncSession, account_id: int, credits: CreditRetention) -> None:
    """Erase the account's private data and leave its row as a tombstone.

    Raises:
        AccountNotFoundError: The account does not exist or was already deleted.
        AccountDeletionBlockedError: Submissions or votes still need the account.
    """
    account = await session.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None or account.deleted_at is not None:
        raise AccountNotFoundError(account_id)

    # Erasing drafts first locks them, so none can be submitted after the count below.
    await erase_unsubmitted_drafts(session, account_id)
    pending_submissions = await count_unsettled_submissions(session, account_id)
    open_votes = await count_open_vote_sessions(session, account_id)
    if pending_submissions or open_votes:
        raise AccountDeletionBlockedError(pending_submissions=pending_submissions, open_votes=open_votes)

    parameters = {"account": account_id}
    await session.execute(text(_LOCK_CREDITED_BUILDS), parameters)
    if credits is CreditRetention.KEEP_NAME:
        await session.execute(text(_KEEP_NAMES), parameters)
    else:
        builds = sorted(set((await session.scalars(text(_REMOVE_CREDITS), parameters)).all()))
        if builds:
            await session.execute(text(_RENUMBER_CREDITS), {"builds": builds})
    await session.execute(
        text("UPDATE build_credits SET proposed_account_id = NULL WHERE proposed_account_id = :account"), parameters
    )
    await session.execute(
        update(CreatorAlias)
        .where(CreatorAlias.account_id == account_id)
        .values(account_id=None, claimed_at=None, claim_method=None)
    )

    for reference in ERASED_REFERENCES:
        rows = table(reference.table_name, column(reference.column_name))
        await session.execute(delete(rows).where(rows.c[reference.column_name] == account_id))

    account.deleted_at = Instant.now()
    account.privacy_notice_version = None
    account.privacy_notice_acknowledged_at = None
    account.discord_build_indexing_enabled = False
    account.revision += 1
    await session.flush()
