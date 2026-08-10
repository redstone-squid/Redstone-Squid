"""Authoritative account and Java-identity checks for device approval."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import CURRENT_CONSENT_VERSION, IdentityProvider
from squid.accounts.infrastructure.models import Account, AccountIdentity


class PostgresAccountIdentityAuthorizer:
    """Read current consent and verified Java identity ownership from PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def has_current_consent(self, account_id: int) -> bool:
        """Return whether an extant account has a complete current consent receipt."""
        async with self._session_factory() as session:
            result = await session.scalar(
                select(Account.id).where(
                    Account.id == account_id,
                    Account.consent_version == CURRENT_CONSENT_VERSION,
                    Account.consented_at.is_not(None),
                )
            )
            return result is not None

    async def can_approve(self, *, account_id: int, java_uuid: UUID) -> bool:
        """Return whether current consent and the exact verified Java UUID coexist on one account."""
        async with self._session_factory() as session:
            result = await session.scalar(
                select(AccountIdentity.id)
                .join(Account, Account.id == AccountIdentity.account_id)
                .where(
                    Account.id == account_id,
                    Account.consent_version == CURRENT_CONSENT_VERSION,
                    Account.consented_at.is_not(None),
                    AccountIdentity.provider == IdentityProvider.JAVA,
                    AccountIdentity.subject == str(java_uuid),
                    AccountIdentity.verified_at.is_not(None),
                )
            )
            return result is not None
