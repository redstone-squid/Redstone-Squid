"""Store range attempts and atomically replace one query's published result."""

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, cast

from pydantic import TypeAdapter
from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.core.pagination import Page, PageSelector
from squid.core.persistence.paging import KeysetOrder
from squid.core.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.core.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery, RangeLabels, RangeTag
from squid.core.records.application.live import DoorRecordCategory
from squid.core.records.application.range_ports import RangeListFilters, RangeRefreshClaim
from squid.core.records.application.range_snapshots import (
    RangeAttempt,
    RangePublication,
    RangeRegistration,
    RangeSnapshotDocument,
    canonical_range_query,
)
from squid.core.records.domain.regions import RegionAnalysisStatus
from squid.core.records.models import (
    DoorRangeRecomputeQueueItem,
    DoorRangeRegistration,
    DoorRangeRun,
    DoorRangeSnapshot,
    RecordSettings,
    RulesRevision,
)
from squid.core.tags.models import TagDefinition
from squid.core.versions.models import Version

_RESULT = TypeAdapter(DoorRangeAnalysis)


RANGE_RECOMPUTE_QUEUE_SPEC = QueueSpec(
    name="door_range_recomputation",
    model=DoorRangeRecomputeQueueItem,
    key=(DoorRangeRecomputeQueueItem.query_id,),
    available_at=DoorRangeRecomputeQueueItem.available_at,
    enqueued_at=DoorRangeRecomputeQueueItem.enqueued_at,
    claimed_at=DoorRangeRecomputeQueueItem.claimed_at,
    claim_token=DoorRangeRecomputeQueueItem.claim_token,
    attempts=DoorRangeRecomputeQueueItem.attempts,
    last_error=DoorRangeRecomputeQueueItem.last_error,
)


class PostgresRangePublicationRepository:
    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session
        self._queue = ClaimedRowQueue(RANGE_RECOMPUTE_QUEUE_SPEC)

    async def register(self, query: DoorRangeQuery) -> int:
        session = self._session()
        canonical = canonical_range_query(query)
        key = hashlib.sha256(canonical.encode()).hexdigest()
        query_id = await session.scalar(
            insert(DoorRangeRegistration)
            .values(query_key=key, query=json.loads(canonical))
            .on_conflict_do_nothing(index_elements=[DoorRangeRegistration.query_key])
            .returning(DoorRangeRegistration.id)
        )
        if query_id is None:
            return (
                await session.execute(select(DoorRangeRegistration.id).where(DoorRangeRegistration.query_key == key))
            ).scalar_one()
        await self._enqueue(query_id)
        return query_id

    async def request_refresh(self, query_id: int) -> bool:
        exists = await self._session().scalar(
            select(DoorRangeRegistration.id).where(DoorRangeRegistration.id == query_id)
        )
        if exists is None:
            return False
        await self._enqueue(query_id)
        return True

    async def _enqueue(self, query_id: int) -> None:
        await self._session().execute(
            insert(DoorRangeRecomputeQueueItem)
            .values(query_id=query_id)
            .on_conflict_do_update(
                index_elements=[DoorRangeRecomputeQueueItem.query_id],
                set_={
                    "enqueued_at": Instant.now(),
                    "available_at": Instant.now(),
                    "claimed_at": None,
                    "claim_token": None,
                },
            )
        )

    async def claim_refresh(self) -> RangeRefreshClaim | None:
        rows = await self._queue.claim(limit=1, session=self._session())
        if not rows:
            return None
        row = rows[0]
        registration = await self._session().get(DoorRangeRegistration, row.query_id)
        assert registration is not None
        return RangeRefreshClaim(
            row.query_id, self._queue.token_of(row), DoorRangeQuery.model_validate(registration.query)
        )

    async def complete_refresh(self, claim: RangeRefreshClaim) -> None:
        await self._queue.complete_batch((claim.token,), session=self._session())

    async def fail_refresh(self, claim: RangeRefreshClaim, error: str) -> None:
        await self._queue.fail_batch((claim.token,), error=error, session=self._session())

    async def list_registered(self) -> Sequence[tuple[int, DoorRangeQuery]]:
        rows = await self._session().execute(select(DoorRangeRegistration).order_by(DoorRangeRegistration.id))
        return tuple((r.id, DoorRangeQuery.model_validate(r.query)) for r in rows.scalars())

    async def begin(self, query_id: int, rules_revision_id: int) -> RangeAttempt:
        session = self._session()
        registration = (
            await session.execute(
                select(DoorRangeRegistration)
                .where(DoorRangeRegistration.id == query_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        registration.generation += 1
        run = DoorRangeRun(query_id=query_id, generation=registration.generation, rules_revision_id=rules_revision_id)
        session.add(run)
        await session.flush()
        return _attempt(run, None)

    async def save_snapshot(self, run_id: int, document: RangeSnapshotDocument) -> None:
        session = self._session()
        run = (
            await session.execute(select(DoorRangeRun).where(DoorRangeRun.id == run_id).with_for_update())
        ).scalar_one()
        registration = await session.get(DoorRangeRegistration, run.query_id)
        if (
            run.completed_at is not None
            or registration is None
            or (document.query.follow_current_version and document.resolved_version_id is None)
            or canonical_range_query(document.query)
            != canonical_range_query(DoorRangeQuery.model_validate(registration.query))
        ):
            msg = "Snapshot does not match the pending range query."
            raise InvalidStateError(msg)
        session.add(
            DoorRangeSnapshot(run_id=run_id, document=document.model_dump(mode="json"), content_hash=document.digest())
        )
        await session.flush()

    async def get_snapshot(self, run_id: int) -> RangeSnapshotDocument | None:
        snapshot = await self._session().get(DoorRangeSnapshot, run_id)
        return None if snapshot is None else RangeSnapshotDocument.model_validate(snapshot.document)

    async def finish(self, run_id: int, result: DoorRangeAnalysis | None, *, error: str | None = None) -> None:
        session = self._session()
        await session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended("record-rules-revision-activation", 0)))
        )
        run = (await session.execute(select(DoorRangeRun).where(DoorRangeRun.id == run_id))).scalar_one()
        settings = await session.scalar(select(RecordSettings).where(RecordSettings.id == 1).with_for_update(read=True))
        # All activation writers lock the registration first, including stale
        # workers. Finishing an older attempt cannot regress the active result.
        registration = (
            await session.execute(
                select(DoorRangeRegistration)
                .where(DoorRangeRegistration.id == run.query_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        await session.refresh(run, with_for_update=True)
        if run.completed_at is not None:
            msg = "Range attempt has already completed."
            raise InvalidStateError(msg)
        if (result is None) == (error is None):
            msg = "Supply one range result or failure."
            raise InvalidStateError(msg)
        snapshot = await session.get(DoorRangeSnapshot, run_id)
        if result is not None and snapshot is None:
            msg = "Range results require a saved candidate snapshot."
            raise InvalidStateError(msg)
        document = None if snapshot is None else RangeSnapshotDocument.model_validate(snapshot.document)
        current_version_matches = not DoorRangeQuery.model_validate(registration.query).follow_current_version or (
            settings is not None
            and document is not None
            and document.effective_version_id is not None
            and document.effective_version_id == settings.current_version_id
        )
        run.result = None if result is None else _RESULT.dump_python(result, mode="json")
        run.error = error
        run.completed_at = Instant.now()
        if (
            result is not None
            and result.analysis.status is RegionAnalysisStatus.EXACT
            and run.generation == registration.generation
            and current_version_matches
            and await session.scalar(
                select(RulesRevision.activated_at).where(RulesRevision.id == run.rules_revision_id)
            )
            is not None
        ):
            registration.last_checked_at = run.completed_at
            previous = await session.scalar(
                select(DoorRangeRun).where(DoorRangeRun.query_id == run.query_id, DoorRangeRun.is_active)
            )
            if (
                previous is not None
                and previous.rules_revision_id == run.rules_revision_id
                and previous.result == run.result
            ):
                previous_snapshot = await session.get(DoorRangeSnapshot, previous.id)
                assert document is not None
                if (
                    previous_snapshot is not None
                    and RangeSnapshotDocument.model_validate(previous_snapshot.document).semantic_digest()
                    == document.semantic_digest()
                ):
                    await session.flush()
                    return
            await session.execute(
                update(DoorRangeRun)
                .where(DoorRangeRun.query_id == run.query_id, DoorRangeRun.is_active)
                .values(is_active=False)
            )
            run.is_active = True
        await session.flush()

    async def get(self, query_id: int) -> RangePublication | None:
        # One statement prevents activation between reading active and latest.
        rows = (
            await self._session().execute(
                select(
                    DoorRangeRegistration,
                    DoorRangeRun,
                    DoorRangeSnapshot,
                    DoorRangeRecomputeQueueItem.query_id.is_not(None),
                )
                .outerjoin(
                    DoorRangeRun,
                    and_(
                        DoorRangeRun.query_id == DoorRangeRegistration.id,
                        or_(DoorRangeRun.is_active, DoorRangeRun.generation == DoorRangeRegistration.generation),
                    ),
                )
                .outerjoin(DoorRangeSnapshot, DoorRangeSnapshot.run_id == DoorRangeRun.id)
                .outerjoin(
                    DoorRangeRecomputeQueueItem, DoorRangeRecomputeQueueItem.query_id == DoorRangeRegistration.id
                )
                .where(DoorRangeRegistration.id == query_id)
                .order_by(DoorRangeRun.generation.desc())
            )
        ).all()
        if not rows:
            return None
        registration = rows[0][0]
        attempts = tuple(
            _attempt(matched_run, snapshot)
            for _, run, snapshot, _pending in rows
            if (matched_run := cast(DoorRangeRun | None, run)) is not None
        )
        return RangePublication(
            registration.id,
            DoorRangeQuery.model_validate(registration.query),
            next((a for a in attempts if a.is_active), None),
            attempts[0] if attempts else None,
            last_checked_at=None if registration.last_checked_at is None else registration.last_checked_at.to_stdlib(),
            refresh_pending=rows[0][3],
        )

    async def list_registrations(
        self, filters: RangeListFilters, *, selector: PageSelector, page_size: int
    ) -> Page[RangeRegistration]:
        session = self._session()
        active = aliased(DoorRangeRun)
        pending = select(DoorRangeRecomputeQueueItem.query_id).where(
            DoorRangeRecomputeQueueItem.query_id == DoorRangeRegistration.id
        )
        listing = _filtered(
            select(DoorRangeRegistration, active.completed_at, pending.exists().label("refresh_pending")).outerjoin(
                active, (active.query_id == DoorRangeRegistration.id) & active.is_active
            ),
            filters,
        )
        order = KeysetOrder("newest", DoorRangeRegistration.id, DoorRangeRegistration.id, descending=True)
        total = await session.scalar(_filtered(select(func.count()).select_from(DoorRangeRegistration), filters))
        page = await order.page(session, listing, selector, page_size=page_size, total=total or 0)
        return replace(
            page,
            items=tuple(
                RangeRegistration(
                    registration.id,
                    DoorRangeQuery.model_validate(registration.query),
                    None if published_at is None else published_at.to_stdlib(),
                    None if registration.last_checked_at is None else registration.last_checked_at.to_stdlib(),
                    refresh_pending,
                )
                for registration, published_at, refresh_pending in page.items
            ),
        )

    async def labels(self, queries: Sequence[tuple[DoorRecordCategory, int | None]]) -> tuple[RangeLabels, ...]:
        session = self._session()
        tag_ids = {tag_id for query, _ in queries for tag_id in (*query.type_ids, *query.restriction_ids)}
        version_ids = {version_id for _, version_id in queries if version_id is not None}
        tags = {
            tag_id: (name, slug)
            for tag_id, name, slug in await session.execute(
                select(TagDefinition.id, TagDefinition.display_name, TagDefinition.slug).where(
                    TagDefinition.id.in_(tag_ids)
                )
            )
        }
        versions = {
            version.id: version.name
            for version in await session.scalars(select(Version).where(Version.id.in_(version_ids)))
        }

        def described(query: DoorRecordCategory, version_id: int | None) -> RangeLabels:
            thresholds = {tag_id: (operator, value) for tag_id, operator, value in query.restriction_values}
            return RangeLabels(
                types=tuple(RangeTag(tag_id, *tags[tag_id]) for tag_id in sorted(query.type_ids) if tag_id in tags),
                restrictions=tuple(
                    RangeTag(tag_id, *tags[tag_id], *thresholds.get(tag_id, (None, None)))
                    for tag_id in sorted(query.restriction_ids)
                    if tag_id in tags
                ),
                version=versions.get(version_id) if version_id is not None else None,
            )

        return tuple(described(query, version_id) for query, version_id in queries)


def _filtered[S: Select[*tuple[Any, ...]]](statement: S, filters: RangeListFilters) -> S:
    query = DoorRangeRegistration.query
    if filters.orientation is not None:
        statement = statement.where(query["orientation"].astext == filters.orientation)
    if filters.height is not None:
        statement = statement.where(query["height"].as_integer() == filters.height)
    if filters.depth is not None:
        statement = statement.where(query["depth"].as_integer() == filters.depth)
    return statement


def _attempt(run: DoorRangeRun, snapshot: DoorRangeSnapshot | None) -> RangeAttempt:
    document = None if snapshot is None else RangeSnapshotDocument.model_validate(snapshot.document)
    return RangeAttempt(
        run.id,
        run.query_id,
        run.generation,
        run.rules_revision_id,
        run.started_at.to_stdlib(),
        None if run.completed_at is None else run.completed_at.to_stdlib(),
        run.is_active,
        None if run.result is None else _RESULT.validate_python(run.result),
        run.error,
        None if snapshot is None else snapshot.content_hash,
        None if document is None else document.captured_at,
        resolved_version_id=None if document is None else document.effective_version_id,
    )
