"""PostgreSQL opaque-session repository."""

from collections.abc import Callable
from typing import override

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.accounts.domain import consent_refresh_required
from squid.accounts.models import Account
from squid.auth.application.ports import WebSessionRepository
from squid.auth.domain.sessions import WebSessionIdentity
from squid.auth.models.sessions import OAuthState, WebSession


class PostgresWebSessionRepository(WebSessionRepository):
    """Store OAuth state and browser sessions in the caller's transaction.

    Methods flush changes but do not commit. The caller must commit state
    consumption before performing an external OAuth exchange.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    @override
    async def save_state(self, state: OAuthState) -> None:
        session = self._session()
        session.add(state)
        await session.flush()

    @override
    async def consume_state(self, state: str, *, now: Instant) -> OAuthState | None:
        """Delete the state and return it if unexpired; expired state is also deleted."""
        session = self._session()
        model = await session.scalar(delete(OAuthState).where(OAuthState.state == state).returning(OAuthState))
        await session.flush()
        if model is None or model.expires_at <= now:
            return None
        return model

    @override
    async def create_session(
        self,
        *,
        token_hash: bytes,
        account_id: int,
        expires_at: Instant,
        user_agent: str | None,
    ) -> str:
        session = self._session()
        model = WebSession(
            token_hash=token_hash,
            account_id=account_id,
            expires_at=expires_at,
            user_agent=user_agent,
        )
        session.add(model)
        await session.flush()
        return str(model.id)

    @override
    async def authenticate(self, token_hash: bytes, *, now: Instant) -> WebSessionIdentity | None:
        """Return an active session identity and update last_seen_at, or None if invalid."""
        session = self._session()
        row = (
            await session.execute(
                select(WebSession, Account)
                .join(Account, Account.id == WebSession.account_id)
                .where(
                    WebSession.token_hash == token_hash,
                    WebSession.revoked_at.is_(None),
                    WebSession.expires_at > now,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        web_session, account = row
        await session.execute(update(WebSession).where(WebSession.id == web_session.id).values(last_seen_at=now))
        await session.flush()
        return WebSessionIdentity(
            str(web_session.id),
            account.id,
            consent_refresh_required(account.created_at, account.consent_version),
        )

    @override
    async def revoke(self, token_hash: bytes, *, now: Instant) -> None:
        session = self._session()
        await session.execute(update(WebSession).where(WebSession.token_hash == token_hash).values(revoked_at=now))
        await session.flush()
