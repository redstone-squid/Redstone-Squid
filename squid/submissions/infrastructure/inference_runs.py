"""Claim-fenced persistence for inference inputs and stable candidates."""

from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as SQLUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.builds.domain import BuildDraft
from squid.core.errors import AuthorizationError, ConflictError, JSONValue
from squid.persistence.advisory_locks import AdvisoryLockNamespace, lock_key
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC
from squid.submissions.application.inference_runs import (
    InferenceCandidate,
    InferenceClaim,
    candidate_id,
    decode_facts,
    encode_facts,
    inference_busy,
)
from squid.submissions.errors import DraftCapacityExceededError


class SubmissionInferenceRun(Base, kw_only=True):
    """Exact private inference inputs and retained candidate facts under a renewable claim."""

    __tablename__ = "submission_inference_runs"
    __table_args__ = (
        CheckConstraint("state IN ('processing', 'failed', 'completed')", name="submission_inference_runs_state_check"),
    )
    id: Mapped[UUID] = mapped_column(SQLUUID(as_uuid=True), primary_key=True)
    owner_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    inputs: Mapped[dict[str, JSONValue]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), index=True)
    claim_token: Mapped[UUID | None] = mapped_column(SQLUUID(as_uuid=True))
    claim_expires_at: Mapped[Instant | None] = mapped_column(InstantUTC())
    candidates: Mapped[list[dict[str, JSONValue]]] = mapped_column(JSONB, default_factory=list)


class PostgresInferenceRuns:
    """Serialize run admission and reject stale invocation results."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def begin(self, run_id: UUID, owner: int, inputs: dict[str, JSONValue], *, capacity: int) -> InferenceClaim:
        async with self._sessions.begin() as session:
            await lock_key(session, "inference-runs", namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_CAPACITY)
            now = await session.scalar(select(func.clock_timestamp()))
            assert now is not None
            current = Instant(now)
            row = await session.get(SubmissionInferenceRun, run_id, with_for_update=True)
            if row is not None:
                if row.owner_account_id != owner:
                    raise AuthorizationError
                if row.expires_at <= current:
                    message = "This inference run has expired."
                    raise ConflictError(message)
                if row.inputs != inputs:
                    message = "An inference run cannot be reused with different inputs."
                    raise ConflictError(message)
                if row.state == "completed":
                    return InferenceClaim(None, _candidates(row))
                if row.claim_expires_at is not None and row.claim_expires_at > current:
                    raise inference_busy()
            else:
                count = await session.scalar(
                    select(func.count())
                    .select_from(SubmissionInferenceRun)
                    .where(SubmissionInferenceRun.expires_at > func.now())
                )
                if (count or 0) >= capacity:
                    raise DraftCapacityExceededError(capacity)
                row = SubmissionInferenceRun(
                    id=run_id,
                    owner_account_id=owner,
                    inputs=inputs,
                    state="processing",
                    expires_at=current.add(days=7, days_assumed_24h_ok=True),
                    claim_token=None,
                    claim_expires_at=None,
                )
                session.add(row)
            row.state = "processing"
            row.claim_token = uuid4()
            row.claim_expires_at = current.add(minutes=5)
            return InferenceClaim(row.claim_token)

    async def complete(
        self, run_id: UUID, token: UUID, drafts: tuple[BuildDraft, ...]
    ) -> tuple[InferenceCandidate, ...]:
        async with self._sessions.begin() as session:
            row = await self._claim(session, run_id, token)
            if row is None:
                raise inference_busy()
            row.candidates = [encode_facts(draft) for draft in drafts]
            row.state = "completed"
            row.claim_token = None
            row.claim_expires_at = None
            return _candidates(row)

    async def fail(self, run_id: UUID, token: UUID) -> None:
        async with self._sessions.begin() as session:
            row = await self._claim(session, run_id, token)
            if row is not None:
                row.state = "failed"
                row.claim_token = None
                row.claim_expires_at = None

    async def expired(self) -> tuple[tuple[UUID, int], ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(SubmissionInferenceRun)
                .where(SubmissionInferenceRun.expires_at <= func.now())
                .order_by(SubmissionInferenceRun.expires_at)
                .limit(100)
            )
            result: list[tuple[UUID, int]] = []
            for row in rows:
                images = row.inputs.get("images", [])
                assert isinstance(images, list)
                result.append((row.id, len(images)))
            return tuple(result)

    async def delete_expired(self, run_id: UUID) -> None:
        from sqlalchemy import delete

        async with self._sessions.begin() as session:
            await session.execute(
                delete(SubmissionInferenceRun).where(
                    SubmissionInferenceRun.id == run_id, SubmissionInferenceRun.expires_at <= func.now()
                )
            )

    async def _claim(self, session: AsyncSession, run_id: UUID, token: UUID) -> SubmissionInferenceRun | None:
        return await session.scalar(
            select(SubmissionInferenceRun)
            .where(
                SubmissionInferenceRun.id == run_id,
                SubmissionInferenceRun.claim_token == token,
                SubmissionInferenceRun.claim_expires_at > func.clock_timestamp(),
                SubmissionInferenceRun.expires_at > func.clock_timestamp(),
            )
            .with_for_update()
        )


def _candidates(row: SubmissionInferenceRun) -> tuple[InferenceCandidate, ...]:
    return tuple(
        InferenceCandidate(candidate_id(row.id, index), row.id, row.owner_account_id, decode_facts(facts))
        for index, facts in enumerate(row.candidates)
    )
