"""Explicit transaction attribution for database-owned build history."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from anyio import CancelScope
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from squid.builds.history import BuildEditContext


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
            {"context": json.dumps(asdict(context), default=str)},
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
