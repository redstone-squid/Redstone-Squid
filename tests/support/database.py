"""Helpers for observing real database operations in integration tests."""

from collections.abc import Collection, Iterator
from contextlib import contextmanager

from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.base import Base


def assert_loaded_detached(entity: Base, *, attributes: Collection[str] | None = None) -> None:
    """Assert that an entity is detached and the selected attributes are already loaded.

    Defaults to scalar columns; relationships must be named explicitly. The check inspects
    loader state without accessing values or issuing SQL.
    """
    state = inspect(entity)
    assert state.detached
    required = set(state.mapper.column_attrs.keys()) if attributes is None else set(attributes)
    missing = required & state.unloaded
    assert not missing, f"Unloaded attributes: {missing}"


@contextmanager
def record_queries(sessions: async_sessionmaker[AsyncSession]) -> Iterator[list[str]]:
    """Collect statements reaching before_cursor_execute on the factory's bound engine.

    The listener also sees other sessions using that engine. Statements that fail during
    execution are included; failures before the cursor event are not.
    """
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
