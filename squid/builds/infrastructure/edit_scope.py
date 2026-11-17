"""Explicit transaction attribution for database-owned build history."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from anyio import CancelScope
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from squid.builds.history import BuildEditContext, HistoryOperation


class _BuildEditContextJson(BaseModel):
    """Attribution document consumed by the PostgreSQL history trigger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: HistoryOperation
    source: str
    actor_account_id: Annotated[int, Field(strict=True, ge=1)] | None
    actor_system: str | None
    correlation_kind: str | None
    correlation_id: str | None
    operation_id: UUID


@asynccontextmanager
async def build_edit_scope(session: AsyncSession, context: BuildEditContext) -> AsyncIterator[None]:
    """Attribute one transaction's build edits without committing it.

    ORM changes flush before the scope closes. Commit may occur inside or after
    the scope; meaningful writes after it closes are rejected by PostgreSQL.
    Exceptions roll back the transaction even when a caller catches them outside
    this scope. A transaction cannot open a second scope or replace attribution.
    """
    with session.no_autoflush:
        await session.execute(
            text("SELECT public.begin_build_edit(CAST(:context AS jsonb))"),
            {"context": _BuildEditContextJson.model_validate(asdict(context)).model_dump_json()},
        )
    transaction = session.get_transaction()
    try:
        yield
        if session.get_transaction() is transaction:
            await session.flush()
            await session.execute(text("SELECT public.close_build_edit()"))
    except BaseException:
        with CancelScope(shield=True):
            await session.rollback()
        raise
