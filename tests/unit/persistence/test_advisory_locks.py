"""Stable advisory-lock wire-contract tests."""

import hashlib
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from squid.persistence.advisory_locks import AdvisoryLockNamespace, lock_key, lock_uuid


@pytest.mark.asyncio
async def test_minecraft_lock_preserves_legacy_sha256_identifier() -> None:
    session = AsyncMock(spec=AsyncSession)
    key = b"fabric:d8de679a-3de4-4cb9-9f11-c961c72a3531:-"

    await lock_key(session, key, namespace=AdvisoryLockNamespace.MINECRAFT_ACTIVE_CHALLENGE)

    statement = session.execute.await_args.args[0]
    expected = int.from_bytes(hashlib.sha256(key).digest()[:8], byteorder="big", signed=True)
    assert expected in statement.compile().params.values()


@pytest.mark.asyncio
async def test_uuid_lock_preserves_namespaced_hashtext_key() -> None:
    session = AsyncMock(spec=AsyncSession)
    identifier = UUID("fa25dd71-162c-44ca-84a7-a4f09bb2df20")

    await lock_uuid(session, identifier, namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_LIFECYCLE)

    statement = session.execute.await_args.args[0]
    assert "submission-draft-lifecycle-v1:fa25dd71-162c-44ca-84a7-a4f09bb2df20" in statement.compile().params.values()
