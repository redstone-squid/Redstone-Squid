"""Namespaced PostgreSQL transaction locks for UUID-owned resources."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE = "submission-draft-lifecycle-v1"


async def lock_uuid(
    session: AsyncSession,
    identifier: UUID,
    *,
    namespace: str,
) -> None:
    """Hold a stable namespaced UUID lock until the current transaction ends."""
    key = f"{namespace}:{identifier}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))
