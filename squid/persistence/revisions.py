"""Shared ORM revision mapping and aggregate checks for application edit handlers."""

from typing import Any

from sqlalchemy import BigInteger, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, MappedAsDataclass, declared_attr, mapped_column
from sqlalchemy.orm.exc import StaleDataError

from squid.edits.domain import ConcurrentEditError, Revision, RevisionConflictError


class Revisioned(MappedAsDataclass, eq=False):
    """Opt-in row version checks. Child and direct-SQL writers must advance the root too."""

    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default=text("1"), init=False)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"version_id_col": cls.revision}


def require_revision(expected: Revision, current: int) -> None:
    if expected.value != current:
        raise RevisionConflictError(expected, current)


async def flush_revision(session: AsyncSession, row: Revisioned, expected: Revision) -> Revision:
    """Flush root changes, advancing its version for a child-only edit when necessary.

    Call only after a real change, while holding the root lock obtained during
    preparation. The mapper handles scalar changes; this also covers child SQL.
    """
    try:
        await session.flush()
        if row.revision == expected.value:
            row.revision += 1
            await session.flush()
    except StaleDataError as error:
        raise ConcurrentEditError from error
    return Revision(expected.target, row.revision)
