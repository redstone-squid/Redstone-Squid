"""Lock keys must coordinate with clients using the established wire identifiers."""

import hashlib
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from squid.persistence.advisory_locks import AdvisoryLockNamespace, lock_key, lock_uuid


async def test_minecraft_lock_preserves_legacy_sha256_identifier(async_engine: AsyncEngine) -> None:
    key = b"fabric:d8de679a-3de4-4cb9-9f11-c961c72a3531:-"
    expected = int.from_bytes(hashlib.sha256(key).digest()[:8], byteorder="big", signed=True)
    async with AsyncSession(async_engine) as owner, AsyncSession(async_engine) as contender:
        await lock_key(owner, key, namespace=AdvisoryLockNamespace.MINECRAFT_ACTIVE_CHALLENGE)
        assert await contender.scalar(select(func.pg_try_advisory_xact_lock(expected))) is False
        await owner.rollback()
        assert await contender.scalar(select(func.pg_try_advisory_xact_lock(expected))) is True


async def test_uuid_lock_preserves_namespaced_hashtext_key(async_engine: AsyncEngine) -> None:
    identifier = UUID("fa25dd71-162c-44ca-84a7-a4f09bb2df20")
    expected = func.hashtextextended(f"submission-draft-lifecycle-v1:{identifier}", 0)
    async with AsyncSession(async_engine) as owner, AsyncSession(async_engine) as contender:
        await lock_uuid(owner, identifier, namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_LIFECYCLE)
        assert await contender.scalar(select(func.pg_try_advisory_xact_lock(expected))) is False
        await owner.rollback()
        assert await contender.scalar(select(func.pg_try_advisory_xact_lock(expected))) is True
