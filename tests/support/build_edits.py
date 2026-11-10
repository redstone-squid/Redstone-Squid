"""Explicit build-edit transactions for fixture setup and repository tests."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.application.ports import BuildEditingUnitOfWork
from squid.builds.history import BuildEditContext
from squid.builds.infrastructure.edit_scope import build_edit_scope
from squid.unit_of_work import UnitOfWorkFactory


@asynccontextmanager
async def build_edit_session(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Commit explicit fixture writes, including raw SQL, with test attribution."""
    async with (
        sessions.begin() as session,
        build_edit_scope(session, BuildEditContext.from_actor("test_fixture", source="fixture")),
    ):
        yield session


async def transact_build_edit[UowT: BuildEditingUnitOfWork, ResultT](
    factory: UnitOfWorkFactory[UowT], operation: Callable[[UowT], Awaitable[ResultT]], *, commit: bool = False
) -> ResultT:
    """Declare fixture attribution for writes; retain the ordinary read-only path."""
    async with factory() as uow:
        if not commit:
            return await operation(uow)
        async with uow.builds.editing(actor="test_fixture", source="fixture"):
            result = await operation(uow)
            await uow.commit()
            return result
