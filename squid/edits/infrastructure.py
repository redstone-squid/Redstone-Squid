"""PostgreSQL storage for edit outcomes."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.edits.domain import EditOutcome, EditTarget, Revision
from squid.edits.models import EditOperation


class PostgresEditStore:
    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def lock_operation(self, operation_id: UUID) -> None:
        # The same lock is taken for execution and reconciliation. Its release
        # proves that a missing outcome is not an operation still committing.
        await self._session().execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended("squid-edit:" + str(operation_id), 0)))
        )

    async def outcome(self, operation_id: UUID) -> EditOutcome | None:
        row = await self._session().get(EditOperation, operation_id)
        if row is None:
            return None
        return EditOutcome(
            row.id,
            row.actor,
            row.fingerprint,
            tuple(
                Revision(EditTarget(str(value["kind"]), str(value["key"])), int(value["revision"]))
                for value in row.revisions
            ),
            row.changed,
            row.final,
        )

    async def record(self, outcome: EditOutcome) -> None:
        now = Instant.now()
        self._session().add(
            EditOperation(
                id=outcome.operation_id,
                actor=outcome.actor,
                fingerprint=outcome.fingerprint,
                revisions=[
                    {"kind": version.target.kind, "key": version.target.key, "revision": version.value}
                    for version in outcome.revisions
                ],
                changed=outcome.changed,
                final=outcome.final,
                created_at=now,
                expires_at=now.add(hours=24),
            )
        )
        await self._session().flush()

    async def purge_expired(self, *, now: Instant) -> int:
        removed = await self._session().scalars(
            delete(EditOperation).where(EditOperation.expires_at <= now).returning(EditOperation.id)
        )
        return len(removed.all())
