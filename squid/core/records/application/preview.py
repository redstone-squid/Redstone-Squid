"""Compare one proposed candidate with the approved population at a point in time."""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from squid.core.actors import ActingContext, application_operation
from squid.core.records.application.measurements import metric_snapshot
from squid.core.records.application.metrics import snapshot_values
from squid.core.records.application.models import CandidateBuild, ComputedRecordResult
from squid.core.records.application.snapshots import RecordSnapshot
from squid.core.records.domain import BuildKind, CandidateGap, RecordCriterion, RecordFact, VersionScope

if TYPE_CHECKING:
    from squid.core.auth.application.viewer import ViewerQueries
    from squid.core.records.application.services import RecordComputationService
    from squid.core.submissions.application.drafts import StoredDraft, SubmissionDraftService
    from squid.core.submissions.application.reviews import SubmissionDetail


@dataclass(frozen=True)
class PreviewGap:
    fact: RecordFact
    fields: tuple[str, ...]
    build_id: int | None


@dataclass(frozen=True)
class MetricDelta:
    metric: str
    unit: str
    candidate: int | str
    holder: int | str
    difference: float


@dataclass(frozen=True)
class PreviewHolder:
    build_id: int
    title: str | None
    deltas: tuple[MetricDelta, ...]


@dataclass(frozen=True)
class PredictedRecord:
    record_id: UUID | None
    category_key: str
    title: str
    criterion: RecordCriterion
    scope: VersionScope
    version_id: int | None
    outcome: Literal["holds", "shares", "ties_later", "loses", "undecided"]
    missing: tuple[PreviewGap, ...]
    current_holders: tuple[PreviewHolder, ...]


@dataclass(frozen=True)
class RecordPreview:
    revision: int | None
    evaluated_at: datetime
    missing_fields: tuple[str, ...]
    records: tuple[PredictedRecord, ...]


_FACT_FIELDS = {
    RecordFact.COMPLETION_AT: ("completion",),
    RecordFact.FIXED_VOLUME: ("capture_width", "capture_height", "capture_depth"),
    RecordFact.OPENING: ("opening_time",),
    RecordFact.OPENING_VISIBLE: ("visible_opening_time",),
    RecordFact.CLOSING: ("closing_time",),
    RecordFact.CLOSING_VISIBLE: ("visible_closing_time",),
    RecordFact.OPENING_RESET: ("opening_reset_time",),
    RecordFact.CLOSING_RESET: ("closing_reset_time",),
    RecordFact.EXTENSION: ("extension_time",),
    RecordFact.RETRACTION: ("retraction_time",),
    RecordFact.EXTENSION_RESET: ("extension_reset_time",),
    RecordFact.RETRACTION_RESET: ("retraction_reset_time",),
}


type RecordKey = tuple[int | None, str, RecordCriterion]


def predicted_record(
    proposed: ComputedRecordResult,
    existing: ComputedRecordResult | None,
    build_id: int,
    record_id: UUID | None,
    titles: dict[int, str | None],
) -> PredictedRecord:
    resolution = proposed.resolution
    relevant = tuple(
        gap
        for gap in resolution.gaps
        if gap.build_id == build_id or build_id in resolution.holder_ids or build_id in resolution.tied_ids
    )
    mine = preview_metrics(proposed, build_id)
    # Publication omits candidates lacking the entry metric. An incomplete proposal can still supply it.
    kind = proposed.category.identity.kind
    primary_time = RecordFact.OPENING if kind == BuildKind.DOOR else RecordFact.RETRACTION
    timing_key = "opening_time" if kind == BuildKind.DOOR else "retraction_time"
    omitted: list[RecordFact] = []
    if proposed.criterion == RecordCriterion.FIRST and "completion_at" not in mine:
        omitted.append(RecordFact.COMPLETION_AT)
    if proposed.criterion in (RecordCriterion.SMALLEST, RecordCriterion.FASTEST_SMALLEST) and "volume" not in mine:
        omitted.append(RecordFact.FIXED_VOLUME)
    if proposed.criterion in (RecordCriterion.FASTEST, RecordCriterion.SMALLEST_FASTEST) and timing_key not in mine:
        omitted.append(primary_time)
    if proposed.criterion == RecordCriterion.FASTEST_SMALLEST and timing_key not in mine:
        volumes = [c.fixed_volume for c in proposed.category.candidates if c.fixed_volume is not None]
        if not volumes or mine.get("volume", (None,))[0] in (None, min(volumes)):
            omitted.append(primary_time)
    relevant = tuple(dict.fromkeys((*relevant, *(CandidateGap(build_id, field) for field in omitted))))
    outcome: Literal["holds", "shares", "ties_later", "loses", "undecided"]
    if relevant:
        outcome = "undecided"
    elif build_id in resolution.holder_ids:
        outcome = "shares" if len(resolution.holder_ids) > 1 else "holds"
    elif build_id in resolution.tied_ids:
        outcome = "ties_later"
    else:
        outcome = "loses"
    holders = []
    for holder in existing.resolution.holder_ids if existing else ():
        assert existing is not None
        metrics = preview_metrics(existing, holder)
        deltas = []
        for metric, (value, unit) in mine.items():
            if metric not in metrics:
                continue
            previous = metrics[metric][0]
            if isinstance(value, int) and isinstance(previous, int):
                diff = value - previous
            elif isinstance(value, str) and isinstance(previous, str):
                diff = (datetime.fromisoformat(value) - datetime.fromisoformat(previous)).total_seconds()
                unit = "seconds"
            else:
                continue
            deltas.append(MetricDelta(metric, unit, value, previous, diff))
        holders.append(PreviewHolder(holder, titles.get(holder), tuple(deltas)))
    return PredictedRecord(
        record_id,
        proposed.category.identity.key,
        proposed.title.title,
        proposed.criterion,
        proposed.scope,
        proposed.version_id,
        outcome,
        tuple(
            PreviewGap(gap.field, _FACT_FIELDS[gap.field], gap.build_id if gap.build_id > 0 else None)
            for gap in relevant
        ),
        tuple(holders),
    )


def preview_metrics(result: ComputedRecordResult, build_id: int) -> dict[str, tuple[int | str, str]]:
    candidate = next(c for c in result.category.candidates if c.build_id == build_id)
    snapshot = metric_snapshot(result, candidate, include_competitor=True)
    return {
        name: (value, unit.value)
        for name, value, unit in snapshot_values(result.category.identity.kind.value, snapshot)
    }


@dataclass(frozen=True)
class PreviewInputs:
    revision: int | None
    snapshot: RecordSnapshot
    candidate: CandidateBuild
    identities: dict[RecordKey, UUID]
    titles: dict[int, str | None]


class PreviewReader(Protocol):
    async def capture(
        self, subject: StoredDraft | SubmissionDetail, context: ActingContext
    ) -> RecordPreview | PreviewInputs: ...


class RecordPreviews:
    def __init__(
        self,
        reader: PreviewReader,
        engine: RecordComputationService,
        drafts: SubmissionDraftService,
        views: ViewerQueries,
    ) -> None:
        self.reader = reader
        self.engine = engine
        self.drafts = drafts
        self.views = views

    @application_operation
    async def draft(self, identifier: UUID, *, context: ActingContext) -> RecordPreview:
        draft = await self.drafts.get_accessible(identifier, context=context)
        return await self._evaluate(draft, context)

    @application_operation
    async def submission(
        self, identifier: UUID, review_session_id: int | None, *, context: ActingContext
    ) -> RecordPreview:
        submission = await self.views.submission(identifier, context=context, review_session_id=review_session_id)
        return await self._evaluate(submission, context)

    async def _evaluate(self, subject: StoredDraft | SubmissionDetail, context: ActingContext) -> RecordPreview:
        inputs = await self.reader.capture(subject, context)
        if isinstance(inputs, RecordPreview):
            return inputs
        results = await self.engine.predict(inputs.snapshot, inputs.candidate, inputs.identities, inputs.titles)
        return RecordPreview(inputs.revision, inputs.snapshot.captured_at, (), results)
