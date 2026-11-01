"""Helpers for observing real database operations in integration tests."""

from collections.abc import Collection, Iterator
from contextlib import contextmanager

from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.base import Base


def assert_loaded_detached(entity: Base, *, attributes: Collection[str] | None = None) -> None:
    """Check detached access to the requested attributes, defaulting to scalar columns."""
    state = inspect(entity)
    assert state.detached
    required = set(state.mapper.column_attrs.keys()) if attributes is None else set(attributes)
    missing = required & state.unloaded
    assert not missing, f"Unloaded attributes: {missing}"


@contextmanager
def record_queries(sessions: async_sessionmaker[AsyncSession]) -> Iterator[list[str]]:
    """Collect executed statements until the context exits, including failed operations."""
    statements: list[str] = []
    engine = sessions.kw["bind"].sync_engine

    def before_cursor_execute(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
