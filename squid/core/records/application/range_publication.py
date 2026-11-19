"""Capture immutable inputs, compute outside transactions, and publish complete ranges."""

import logging
from contextlib import suppress
from datetime import UTC, datetime

import anyio

from squid.core.records.application.expansion_regions import DoorRangeQuery
from squid.core.records.application.ports import DoorRangeAnalyzer
from squid.core.records.application.published_points import (
    PublishedDoorPoint,
)
from squid.core.records.application.range_ports import RangePublicationUnitOfWork
from squid.core.records.application.range_snapshots import RangePublication, RangeSnapshotDocument, RangeSource
from squid.core.records.application.services import RecordComputationService
from squid.core.records.domain.regions import DEFAULT_REGION_LIMITS, RegionAnalysisStatus
from squid.unit_of_work import UnitOfWorkFactory

logger = logging.getLogger(__name__)


class RangePublicationService:
    """Publish registered queries independently from the fixed-title catalogue.

    Registration opts a query into door recomputation. Each attempt reserves its
    generation before reading candidates, saves its snapshot before arithmetic,
    and activates only if no newer attempt has been reserved. Failed analyses
    remain inspectable while readers retain the previous exact result.
    """

    def __init__(
        self,
        computation: RecordComputationService,
        *,
        uow_factory: UnitOfWorkFactory[RangePublicationUnitOfWork],
        analyzer: DoorRangeAnalyzer,
    ) -> None:
        self._computation = computation
        self._uow_factory = uow_factory
        self._analyzer = analyzer
        self._limiter = anyio.CapacityLimiter(1)

    async def register(self, query: DoorRangeQuery) -> int:
        """Register a query and queue its initial computation; duplicates reuse its ID."""
        async with self._uow_factory() as uow:
            query_id = await uow.record_ranges.register(query)
            await uow.commit()
        return query_id

    async def request_refresh(self, query_id: int) -> bool:
        """Queue existing registered ranges without running arithmetic in the request."""
        async with self._uow_factory() as uow:
            found = await uow.record_ranges.request_refresh(query_id)
            await uow.commit()
        return found

    async def get(self, query_id: int) -> RangePublication | None:
        """Read publication metadata and saved results without consulting live builds."""
        async with self._uow_factory() as uow:
            return await uow.record_ranges.get(query_id)

    async def get_point(self, query_id: int, width: int) -> PublishedDoorPoint | None:
        """Select the published region covering width without reading live builds."""
        publication = await self.get(query_id)
        return None if publication is None else PublishedDoorPoint.from_publication(publication, width)

    async def process_queue(self, *, limit: int = 20) -> tuple[int, int]:
        """Attempt due ranges independently, retaining failed work for retry."""
        # Revision activation may enqueue every range. Do it before claiming,
        # so initial activation cannot invalidate the claim we are about to use.
        async with self._uow_factory() as uow:
            await uow.records.active_rules_revision_id()
            await uow.commit()
        succeeded = failed = 0
        for _ in range(limit):
            async with self._uow_factory() as uow:
                claim = await uow.record_ranges.claim_refresh()
                await uow.commit()
            if claim is None:
                break
            error: str | None = None
            try:
                publication = await self.publish(claim.query)
                latest = publication.latest
                if latest is None or latest.error is not None:
                    error = latest.error if latest is not None else "missing_attempt"
                elif latest.result is None or latest.result.analysis.status is not RegionAnalysisStatus.EXACT:
                    error = "inexact_analysis" if latest.result is None else latest.result.analysis.status.value
            except Exception as exc:
                logger.exception("Range query %s failed to refresh", claim.query_id)
                error = type(exc).__name__
            async with self._uow_factory() as uow:
                if error is None:
                    await uow.record_ranges.complete_refresh(claim)
                    succeeded += 1
                else:
                    await uow.record_ranges.fail_refresh(claim, error)
                    failed += 1
                await uow.commit()
        return succeeded, failed

    async def publish(self, query: DoorRangeQuery) -> RangePublication:
        """Capture and compute a fresh attempt, returning active and latest outcomes."""
        async with self._limiter:
            async with self._uow_factory() as uow:
                revision = await uow.records.active_rules_revision_id()
                query_id = await uow.record_ranges.register(query)
                version_id = (
                    await uow.records.active_current_version_id() if query.follow_current_version else query.version_id
                )
                attempt = await uow.record_ranges.begin(query_id, revision)
                await uow.commit()
            try:
                if query.follow_current_version and version_id is None:
                    await self._fail(attempt.id, "current_version_unset")
                    return await self._published(query_id)
                async with self._uow_factory() as uow:
                    sources = await uow.record_candidates.list_for_door_widths(
                        query.height,
                        query.depth,
                        query.orientation,
                        version_id,
                        minimum=query.minimum,
                        maximum=query.maximum,
                    )
                    captured_at = datetime.now(UTC)
                candidates = self._computation.door_range_candidates(query, sources)
                if len(candidates) > DEFAULT_REGION_LIMITS.candidates:
                    # The analyzer would reject these inputs. Snapshots cannot be
                    # deleted, so do not save one that can never produce a result.
                    await self._fail(attempt.id, "too_many_sources")
                    return await self._published(query_id)
                snapshot = RangeSnapshotDocument(
                    query=query,
                    sources=tuple(RangeSource.from_candidate(s) for s in candidates),
                    captured_at=captured_at,
                    resolved_version_id=version_id,
                )
                async with self._uow_factory() as uow:
                    await uow.record_ranges.save_snapshot(attempt.id, snapshot)
                    await uow.commit()
                # Decode the saved document so arithmetic uses exactly the persisted
                # representation, including its schema's date and numeric semantics.
                async with self._uow_factory() as uow:
                    saved = await uow.record_ranges.get_snapshot(attempt.id)
                assert saved is not None
                result = await self._analyzer.analyze(tuple(s.to_candidate() for s in saved.sources), saved.query)
                async with self._uow_factory() as uow:
                    await uow.record_ranges.finish(attempt.id, result)
                    await uow.commit()
            except anyio.get_cancelled_exc_class():
                await self._fail(attempt.id, "cancelled")
                raise
            except Exception as error:
                await self._fail(attempt.id, type(error).__name__)
                raise
        return await self._published(query_id)

    async def _published(self, query_id: int) -> RangePublication:
        publication = await self.get(query_id)
        assert publication is not None
        return publication

    async def _fail(self, run_id: int, reason: str) -> None:
        # Preserve the original failure even if the database is unavailable.
        # A process kill can still leave an unfinished attempt; the queue lease
        # will expire and the next worker reserves a new generation.
        with anyio.move_on_after(5, shield=True), suppress(Exception):
            async with self._uow_factory() as uow:
                await uow.record_ranges.finish(run_id, None, error=reason)
                await uow.commit()
