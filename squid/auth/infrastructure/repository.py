"""PostgreSQL API-key repository."""

from collections.abc import Callable

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.auth.models.entities import ApiKey
from squid.permissions.domain import Pattern


class PostgresApiKeyRepository:
    """Store API-key digests and usage metadata in the caller's transaction."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def add(
        self,
        *,
        key_id: str,
        secret_hash: bytes,
        label: str,
        scopes: frozenset[Pattern],
        owner_account_id: int | None,
        created_by_account_id: int | None,
        expires_at: Instant | None,
    ) -> ApiKey:
        """Insert and return an API credential."""
        session = self._session()
        model = ApiKey(
            key_id=key_id,
            secret_hash=secret_hash,
            label=label,
            scopes=scopes,
            owner_account_id=owner_account_id,
            created_by_account_id=created_by_account_id,
            expires_at=expires_at,
        )
        session.add(model)
        await session.flush()
        await session.refresh(model)
        return model

    async def get_by_key_id(self, key_id: str) -> ApiKey | None:
        """Return the credential matching its indexed public ID."""
        session = self._session()
        return await session.scalar(select(ApiKey).where(ApiKey.key_id == key_id))

    async def touch_last_used(
        self,
        key_id: str,
        *,
        used_at: Instant,
        used_ip: str | None,
        older_than: Instant,
    ) -> None:
        """Update usage metadata at most once per service-defined interval."""
        session = self._session()
        await session.scalar(
            update(ApiKey)
            .where(
                ApiKey.key_id == key_id,
                or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at < older_than),
            )
            .values(last_used_at=used_at, last_used_ip=used_ip)
            .returning(ApiKey),
            execution_options={"populate_existing": True},
        )
        await session.flush()
