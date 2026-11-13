"""Claim-fenced persistence for inference inputs and stable candidates."""

from collections.abc import Callable
from typing import override
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.builds.application.inference import BuildInferenceInput
from squid.builds.domain import BuildDraft
from squid.builds.models import Build
from squid.core.errors import AuthorizationError, ConflictError, JSONValue
from squid.persistence.advisory_locks import AdvisoryLockNamespace, lock_key
from squid.submissions.application.inference_runs import (
    InferenceCandidate,
    InferenceClaim,
    InferenceRunRepository,
    InferenceStatus,
    candidate_id,
    inference_busy,
)
from squid.submissions.domain.source_files import SubmissionSourceFile
from squid.submissions.errors import DraftCapacityExceededError
from squid.submissions.infrastructure.inference_payloads import BuildDraftPayload
from squid.submissions.models.entities import SubmissionDraft
from squid.submissions.models.inference import SubmissionInferenceRun

_DRAFT = TypeAdapter(BuildDraftPayload)
_SOURCE_FILES = TypeAdapter(tuple[SubmissionSourceFile, ...])


class PostgresInferenceRunRepository(InferenceRunRepository):
    """Store inference inputs and candidates in the caller's transaction.

    Admission is serialized across all runs. Submission runs share a global
    capacity limit; recalculation runs do not count toward it. Claims last five
    minutes and completion checks both the token and its expiry.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    @override
    async def begin(self, run_id: UUID, owner: int, inputs: dict[str, JSONValue], *, capacity: int) -> InferenceClaim:
        session = self._session()
        await lock_key(session, "inference-runs", namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_CAPACITY)
        now = await session.scalar(select(func.clock_timestamp()))
        assert now is not None
        current = Instant(now)
        row = await session.get(SubmissionInferenceRun, run_id, with_for_update=True)
        if row is not None:
            # A run ID identifies exact inputs, so a retry cannot replace the prompt.
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
            terminal = (
                select(func.count())
                .select_from(SubmissionDraft)
                .where(
                    SubmissionDraft.inference_run_id == SubmissionInferenceRun.id,
                    SubmissionDraft.status.in_(("submitted", "expired")),
                )
                .correlate(SubmissionInferenceRun)
                .scalar_subquery()
            )
            count = await session.scalar(
                select(func.count())
                .select_from(SubmissionInferenceRun)
                .where(
                    SubmissionInferenceRun.expires_at > func.now(),
                    SubmissionInferenceRun.inputs["purpose"].astext.is_distinct_from("recalculation"),
                    or_(
                        SubmissionInferenceRun.state != "completed",
                        func.jsonb_array_length(SubmissionInferenceRun.candidates) > terminal,
                    ),
                )
            )
            if inputs.get("purpose") != "recalculation" and (count or 0) >= capacity:
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

    @override
    async def complete(
        self, run_id: UUID, token: UUID, drafts: tuple[BuildDraft, ...]
    ) -> tuple[InferenceCandidate, ...]:
        session = self._session()
        row = await self._claim(session, run_id, token)
        if row is None:
            raise inference_busy()
        row.candidates = [_DRAFT.dump_python(BuildDraftPayload.from_draft(draft), mode="json") for draft in drafts]
        row.state = "completed"
        row.claim_token = None
        row.claim_expires_at = None
        return _candidates(row)

    @override
    async def fail(self, run_id: UUID, token: UUID) -> None:
        session = self._session()
        row = await self._claim(session, run_id, token)
        if row is not None:
            row.state = "failed"
            row.claim_token = None
            row.claim_expires_at = None

    @override
    async def retained(
        self, run_id: UUID
    ) -> tuple[int, dict[str, JSONValue], bool, tuple[InferenceCandidate, ...]] | None:
        session = self._session()
        row = await session.scalar(
            select(SubmissionInferenceRun).where(
                SubmissionInferenceRun.id == run_id, SubmissionInferenceRun.expires_at > func.now()
            )
        )
        return (
            (row.owner_account_id, row.inputs, row.state == "completed", _candidates(row)) if row is not None else None
        )

    @override
    async def get(self, run_id: UUID) -> tuple[int, tuple[InferenceCandidate, ...]] | None:
        session = self._session()
        row = await session.scalar(
            select(SubmissionInferenceRun).where(
                SubmissionInferenceRun.id == run_id,
                SubmissionInferenceRun.expires_at > func.now(),
                SubmissionInferenceRun.inputs["purpose"].astext == "submission",
            )
        )
        if row is None:
            return None
        return row.owner_account_id, _candidates(row)

    @override
    async def public_status(self, run_id: UUID) -> InferenceStatus | None:
        session = self._session()
        row = await session.get(SubmissionInferenceRun, run_id)
        if row is None or row.inputs.get("purpose") != "submission" or row.expires_at <= Instant.now():
            return None
        bundle = TypeAdapter(BuildInferenceInput).validate_python(row.inputs["bundle"])
        drafts = {
            item.id: item
            for item in await session.scalars(select(SubmissionDraft).where(SubmissionDraft.inference_run_id == run_id))
        }
        saved = {
            draft_id: build_id
            for draft_id, build_id in await session.execute(
                select(Build.source_submission_draft_id, Build.id).where(Build.source_submission_draft_id.in_(drafts))
            )
        }
        states = tuple(
            f"saved as build #{saved[candidate.id]}"
            if candidate.id in saved
            else drafts[candidate.id].status.value.replace("_", " ")
            if candidate.id in drafts
            else "needs category"
            if candidate.facts.category is None
            else "awaiting draft admission"
            for candidate in _candidates(row)
        )
        return InferenceStatus(bundle.channel_id, bundle.server_id, bundle.primary[0].message_id, row.state, states)

    @override
    async def list_active(self, owner: int | None) -> tuple[UUID, ...]:
        session = self._session()
        query = select(SubmissionInferenceRun.id).where(
            SubmissionInferenceRun.expires_at > func.now(),
            SubmissionInferenceRun.inputs["purpose"].astext == "submission",
        )
        if owner is not None:
            query = query.where(SubmissionInferenceRun.owner_account_id == owner)
        return tuple(await session.scalars(query.order_by(SubmissionInferenceRun.expires_at.desc()).limit(10)))

    @override
    async def expired(self) -> tuple[tuple[UUID, int], ...]:
        session = self._session()
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

    @override
    async def delete_expired(self, run_id: UUID) -> None:
        session = self._session()
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
        InferenceCandidate(
            candidate_id(row.id, index),
            row.id,
            row.owner_account_id,
            _DRAFT.validate_python(facts).to_draft(),
            _SOURCE_FILES.validate_python(row.inputs.get("source_files", [])),
        )
        for index, facts in enumerate(row.candidates)
    )
