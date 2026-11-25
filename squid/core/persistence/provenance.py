"""Transaction-local attribution read by PostgreSQL queue and event triggers."""

import json
from base64 import b64encode
from uuid import UUID

from sqlalchemy import BigInteger, Identity, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.core.actors import current_acting_context
from squid.core.persistence.base import Base
from squid.core.persistence.types import InstantUTC
from squid.core.worker_context import current_claim_reference


class WorkActivity(Base, kw_only=True):
    """Committed queue transitions and event publications with their executing invocation."""

    __tablename__ = "work_activity"
    __table_args__ = (
        Index("work_activity_item_idx", "relation", "item", "id"),
        Index("work_activity_claim_idx", "acquired_claim", postgresql_where=text("acquired_claim IS NOT NULL")),
        Index("work_activity_invocation_idx", "invocation_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[Instant] = mapped_column(InstantUTC(), server_default=func.clock_timestamp())
    relation: Mapped[str] = mapped_column(Text)
    item: Mapped[dict[str, object]] = mapped_column(JSONB)
    transition: Mapped[str] = mapped_column(Text)
    acquired_claim: Mapped[str | None] = mapped_column(Text)
    parent_claim: Mapped[str | None] = mapped_column(Text)
    invocation_id: Mapped[UUID | None]
    operator: Mapped[str | None] = mapped_column(Text)
    effective: Mapped[str | None] = mapped_column(Text)
    credential_id: Mapped[str | None] = mapped_column(Text)
    impersonation_session_id: Mapped[UUID | None]
    impersonation_use_id: Mapped[UUID | None]


async def bind_work_attribution(session: AsyncSession, *, reset: bool = False) -> None:
    """Make the invocation visible to enqueue triggers until this transaction ends."""
    context = current_acting_context.get()
    if context is None:
        if reset:
            await session.execute(text("SET LOCAL squid.work_attribution = ''"))
        return
    metadata = {
        "invocation_id": str(context.invocation_id),
        "operator": context.operator.reference,
        "effective": context.effective.reference,
        "credential_id": context.operator.credential_reference,
        "impersonation_session_id": str(context.session_id) if context.session_id else None,
        "impersonation_use_id": str(context.effective.audit_reference) if context.effective.audit_reference else None,
        "parent_claim": current_claim_reference.get(),
    }
    # SET LOCAL preserves the caller's ability to select transaction isolation before its first query.
    # The base64 alphabet is safe in this SQL literal; SET does not accept bind parameters.
    encoded = b64encode(json.dumps(metadata).encode()).decode("ascii")
    await session.execute(text(f"SET LOCAL squid.work_attribution = '{encoded}'"))
