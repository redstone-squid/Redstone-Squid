"""PostgreSQL opaque-session repository."""

from typing import override

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION
from squid.accounts.infrastructure.models import Account
from squid.auth.application.web import WebSessionRepository, consent_pending
from squid.auth.domain.sessions import OAuthState, WebSessionIdentity
from squid.auth.infrastructure.session_models import OAuthStateModel, WebSession


class PostgresWebSessionRepository(WebSessionRepository):
    """Persist one-time state and revocable browser sessions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @override
    async def save_state(self, state: OAuthState) -> None:
        async with self._session_factory() as session:
            session.add(
                OAuthStateModel(
                    state=state.state,
                    code_verifier=state.code_verifier,
                    redirect_to=state.redirect_to,
                    expires_at=state.expires_at,
                )
            )
            await session.commit()

    @override
    async def consume_state(self, state: str, *, now: Instant) -> OAuthState | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                delete(OAuthStateModel).where(OAuthStateModel.state == state).returning(OAuthStateModel)
            )
            await session.commit()
            if model is None or model.expires_at <= now:
                return None
            return OAuthState(model.state, model.code_verifier, model.redirect_to, model.expires_at)

    @override
    async def create_session(
        self,
        *,
        token_hash: bytes,
        account_id: int,
        discord_id: int,
        expires_at: Instant,
        user_agent: str | None,
    ) -> str:
        async with self._session_factory() as session:
            model = WebSession(
                token_hash=token_hash,
                account_id=account_id,
                discord_id=discord_id,
                expires_at=expires_at,
                user_agent=user_agent,
            )
            session.add(model)
            await session.commit()
            return str(model.id)

    @override
    async def authenticate(self, token_hash: bytes, *, now: Instant) -> WebSessionIdentity | None:
        async with self._session_factory() as session:
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
            await session.commit()
            return WebSessionIdentity(
                str(web_session.id),
                account.id,
                web_session.discord_id,
                consent_pending(account.created_at, account.consent_version, CURRENT_CONSENT_VERSION),
            )

    @override
    async def revoke(self, token_hash: bytes, *, now: Instant) -> None:
        async with self._session_factory() as session:
            await session.execute(update(WebSession).where(WebSession.token_hash == token_hash).values(revoked_at=now))
            await session.commit()
