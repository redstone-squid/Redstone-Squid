"""Namespaced PostgreSQL transaction locks for application resources."""

import hashlib
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class AdvisoryLockNamespace(StrEnum):
    """Closed namespaces whose existing values are stable lock-wire contracts."""

    SUBMISSION_DRAFT_LIFECYCLE = "submission-draft-lifecycle-v1"
    MEDIA_UPLOAD_REGISTRATION = "media-upload-registration-v1"
    MINECRAFT_ACTIVE_CHALLENGE = "minecraft-active-challenge-v1"


SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE = AdvisoryLockNamespace.SUBMISSION_DRAFT_LIFECYCLE


async def lock_uuid(
    session: AsyncSession,
    identifier: UUID,
    *,
    namespace: AdvisoryLockNamespace,
) -> None:
    """Hold a stable namespaced UUID lock until the current transaction ends."""
    await lock_key(session, str(identifier), namespace=namespace)


async def lock_key(
    session: AsyncSession,
    key: str | bytes,
    *,
    namespace: AdvisoryLockNamespace,
) -> None:
    """Hold a stable namespaced canonical-key lock until the transaction ends."""
    if namespace is AdvisoryLockNamespace.MINECRAFT_ACTIVE_CHALLENGE:
        # This namespace predates the shared helper. Preserve its SHA-256-derived
        # lock IDs so old and new processes still serialize during a rollout.
        payload = key.encode() if isinstance(key, str) else key
        lock_id = int.from_bytes(hashlib.sha256(payload).digest()[:8], byteorder="big", signed=True)
        await session.execute(select(func.pg_advisory_xact_lock(lock_id)))
        return

    canonical_key = key if isinstance(key, str) else f"bytes:{key.hex()}"
    namespaced_key = f"{namespace}:{canonical_key}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(namespaced_key, 0))))
