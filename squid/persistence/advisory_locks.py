"""Namespaced PostgreSQL transaction locks for UUID-owned resources."""

from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class AdvisoryLockNamespace(StrEnum):
    """Closed namespaces whose existing values are stable lock-wire contracts."""

    SUBMISSION_DRAFT_LIFECYCLE = "submission-draft-lifecycle-v1"
    MEDIA_UPLOAD_REGISTRATION = "media-upload-registration-v1"


SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE = AdvisoryLockNamespace.SUBMISSION_DRAFT_LIFECYCLE


async def lock_uuid(
    session: AsyncSession,
    identifier: UUID,
    *,
    namespace: AdvisoryLockNamespace,
) -> None:
    """Hold a stable namespaced UUID lock until the current transaction ends."""
    key = f"{namespace}:{identifier}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))
