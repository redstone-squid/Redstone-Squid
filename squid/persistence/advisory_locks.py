"""Coordinate transactions that operate on the same application resource.

An advisory lock identifies a resource by a key rather than a database row. This
lets repositories protect operations such as checking a draft limit and creating
the next draft, where locking an existing draft alone would not protect the count.
The lock lasts until the transaction ends. Every competing operation must use the
same namespace and key; PostgreSQL does not infer these locks from table writes.
"""

import hashlib
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class AdvisoryLockNamespace(StrEnum):
    """Lock namespaces shared by all application processes.

    Keep existing values stable: processes running different releases must derive
    the same lock ID for the same resource.
    """

    SUBMISSION_DRAFT_LIFECYCLE = "submission-draft-lifecycle-v1"
    SUBMISSION_DRAFT_CAPACITY = "submission-draft-capacity-v1"
    SUBMISSION_REVISION_PROPOSAL = "submission-revision-proposal-v1"
    SUBMISSION_SCHEMATIC_SOURCE = "submission-schematic-source-v1"
    MEDIA_UPLOAD_REGISTRATION = "media-upload-registration-v1"
    MINECRAFT_ACTIVE_CHALLENGE = "minecraft-active-challenge-v1"


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
    """Wait for a resource lock and hold it until the transaction ends.

    Callers must use the same key representation and namespace for the same resource.
    """
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
