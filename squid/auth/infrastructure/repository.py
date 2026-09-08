"""PostgreSQL API-key repository."""

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.auth.domain import ApiKey
from squid.auth.infrastructure.models import ApiKey as ApiKeyModel
from squid.core.errors import DataIntegrityError
from squid.permissions.domain import InvalidPatternError, Pattern


def _to_domain(model: ApiKeyModel) -> ApiKey:
    try:
        scopes = frozenset(Pattern.parse(raw) for raw in model.scopes)
    except InvalidPatternError as error:
        msg = "A stored API key carries an unparsable permission pattern."
        raise DataIntegrityError(msg, context={"key_id": model.key_id}) from error
    return ApiKey(
        id=model.id,
        key_id=model.key_id,
        secret_hash=model.secret_hash,
        label=model.label,
        scopes=scopes,
        owner_account_id=model.owner_account_id,
        created_by_account_id=model.created_by_account_id,
        created_at=model.created_at,
        expires_at=model.expires_at,
        revoked_at=model.revoked_at,
        last_used_at=model.last_used_at,
        last_used_ip=model.last_used_ip,
    )


class PostgresApiKeyRepository:
    """Persist API keys without ever storing their plaintext secrets."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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
        async with self._session_factory() as session:
            model = ApiKeyModel(
                key_id=key_id,
                secret_hash=secret_hash,
                label=label,
                # A set has no order and the column is an array: sort on write so diffs, audit
                # output and fixture comparisons are stable.
                scopes=sorted(pattern.raw for pattern in scopes),
                owner_account_id=owner_account_id,
                created_by_account_id=created_by_account_id,
                expires_at=expires_at,
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return _to_domain(model)

    async def get_by_key_id(self, key_id: str) -> ApiKey | None:
        """Return the credential with this public key ID, revoked or expired ones included.

        Raises `DataIntegrityError` if the stored scopes do not parse as permission patterns.
        """
        async with self._session_factory() as session:
            model = await session.scalar(select(ApiKeyModel).where(ApiKeyModel.key_id == key_id))
            return None if model is None else _to_domain(model)

    async def touch_last_used(
        self,
        key_id: str,
        *,
        used_at: Instant,
        used_ip: str | None,
        older_than: Instant,
    ) -> None:
        """Update usage metadata at most once per service-defined interval."""
        async with self._session_factory() as session:
            await session.execute(
                update(ApiKeyModel)
                .where(
                    ApiKeyModel.key_id == key_id,
                    or_(ApiKeyModel.last_used_at.is_(None), ApiKeyModel.last_used_at < older_than),
                )
                .values(last_used_at=used_at, last_used_ip=used_ip)
            )
            await session.commit()
