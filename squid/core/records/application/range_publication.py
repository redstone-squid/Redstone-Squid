"""Capture immutable inputs, compute outside transactions, and publish complete ranges."""

import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime

import anyio

from squid.core.actors import ActingContext, application_operation
from squid.core.errors import ServiceUnavailableError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import RECORD_ENTRY_REBUILD
from squid.core.records.application.expansion_regions import DoorRangeQuery, DoorRangeResult
from squid.core.records.application.metrics import RecordHolderSummary
from squid.core.records.application.ports import DoorRangeAnalyzer, PublicBuildSummaryReader
from squid.core.records.application.published_points import (
    PublishedDoorPoint,
)
from squid.core.records.application.range_ports import RangeListFilters, RangePublicationUnitOfWork
from squid.core.records.application.range_snapshots import (
    RangePublication,
    RangeRegistration,
    RangeSnapshotDocument,
    RangeSource,
)
from squid.core.records.application.services import RecordComputationService
from squid.core.records.domain.regions import DEFAULT_REGION_LIMITS, RegionAnalysisStatus
from squid.core.unit_of_work import UnitOfWorkFactory
from squid.core.worker_context import worker_execution, worker_scope

logger = logging.getLogger(__name__)


class RangePublicationService:
    """Analyze door width ranges live, and publish registered queries.

    Registration opts a query into door recomputation. Each attempt reserves its
    generation before reading candidates, saves its snapshot before arithmetic,
    and activates only if no newer attempt has been reserved. Failed analyses
    remain inspectable while readers retain the previous exact result.

    Results name their tags and version and summarize their public holders.
    """

    def __init__(
        self,
        computation: RecordComputationService,
        permissions: PermissionService,
        *,
        uow_factory: UnitOfWorkFactory[RangePublicationUnitOfWork],
        analyzer: DoorRangeAnalyzer,
        builds: PublicBuildSummaryReader,
        live_concurrency: int = 2,
    ) -> None:
        self._computation = computation
        self._permissions = permissions
        self._uow_factory = uow_factory
        self._analyzer = analyzer
        self._builds = builds
        self._limiter = anyio.CapacityLimiter(1)
        self._live_limiter = anyio.CapacityLimiter(live_concurrency)

    @application_operation
    async def lookup(self, request: DoorRangeQuery, *, context: ActingContext) -> DoorRangeResult:
        """Analyze live smallest-record widths without publishing or storing regions.

        Admission covers database loading and analysis. The worker receives
        immutable facts after the read-only transaction closes. Saturation fails
        immediately; requests do not queue with loaded candidate documents.
        """
        try:
            self._live_limiter.acquire_nowait()
        except anyio.WouldBlock as error:
            raise ServiceUnavailableError(context={"reason": "range_capacity"}) from error
        try:
            if request.follow_current_version:
                async with self._uow_factory() as uow:
                    version_id = await uow.records.active_current_version_id()
                if version_id is None:
                    raise ServiceUnavailableError(context={"reason": "current_version_unset"})
                request = request.model_copy(update={"version_id": version_id, "follow_current_version": False})
            async with self._uow_factory() as uow:
                sources = tuple(
                    await uow.record_candidates.list_for_door_widths(
                        request.height,
                        request.depth,
                        request.orientation,
                        request.version_id,
                        minimum=request.minimum,
                        maximum=request.maximum,
                    )
                )
            result = await self._analyzer.analyze(self._computation.door_range_candidates(request, sources), request)
        finally:
            self._live_limiter.release()
        holder_ids = (id for region in result.analysis.regions for id in region.resolution.holder_ids)
        async with self._uow_factory() as uow:
            (labels,) = await uow.record_ranges.labels(((request, request.version_id),))
        return DoorRangeResult(
            request,
            datetime.now(UTC),
            result.analysis,
            result.configurations,
            labels=labels,
            holders=await self._holders(tuple(dict.fromkeys(holder_ids)), context=context),
        )

    @application_operation
    async def list_page(
        self,
        filters: RangeListFilters,
        *,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        context: ActingContext,
    ) -> Page[RangeRegistration]:
        """Return registered queries newest first, with their labels."""
        async with self._uow_factory() as uow:
            rows = await uow.record_ranges.list_registrations(filters, selector=selector, limit=page_size + 1)
            page = keyset_page(
                rows,
                selector=selector,
                page_size=page_size,
                total=await uow.record_ranges.count_registrations(filters),
                id_of=lambda registration: registration.query_id,
            )
            # Following the pin names the version it currently resolves to.
            pinned = await uow.records.active_current_version_id()
            labels = await uow.record_ranges.labels(
                tuple(
                    (item.query, pinned if item.query.follow_current_version else item.query.version_id)
                    for item in page.items
                )
            )
        items = tuple(replace(item, labels=label) for item, label in zip(page.items, labels, strict=True))
        return Page(items, page.total, page.next, page.prev)

    async def _holders(self, build_ids: Sequence[int], *, context: ActingContext) -> tuple[RecordHolderSummary, ...]:
        builds = await self._builds.get_public_summaries(build_ids, context=context) if build_ids else ()
        return tuple(RecordHolderSummary.from_build(build) for build in builds)

    async def register(self, query: DoorRangeQuery, *, context: ActingContext) -> int:
        """Register a query and queue its initial computation; duplicates reuse its ID."""
        with context.scope():
            await self._permissions.require(context.effective, RECORD_ENTRY_REBUILD)
            async with self._uow_factory() as uow:
                query_id = await uow.record_ranges.register(query)
                await uow.commit()
        return query_id

    async def request_refresh(self, query_id: int, *, context: ActingContext) -> bool:
        """Queue existing registered ranges without running arithmetic in the request."""
        with context.scope():
            await self._permissions.require(context.effective, RECORD_ENTRY_REBUILD)
            async with self._uow_factory() as uow:
                found = await uow.record_ranges.request_refresh(query_id)
                await uow.commit()
        return found

    @application_operation
    async def get(self, query_id: int, *, context: ActingContext) -> RangePublication | None:
        """Read publication metadata and saved results without consulting live builds."""
        async with self._uow_factory() as uow:
            publication = await uow.record_ranges.get(query_id)
            if publication is None:
                return None
            (labels,) = await uow.record_ranges.labels(((publication.query, publication.version_id),))
        holders = await self._holders(publication.holder_ids, context=context)
        return replace(publication, labels=labels, holders=holders)

    @application_operation
    async def get_point(self, query_id: int, width: int, *, context: ActingContext) -> PublishedDoorPoint | None:
        """Select the published region covering width without reading live builds."""
        publication = await self.get(query_id, context=context)
        return None if publication is None else PublishedDoorPoint.from_publication(publication, width)

    async def process_queue(self, *, context: ActingContext, limit: int = 20) -> tuple[int, int]:
        """Attempt due ranges independently, retaining failed work for retry."""
        # Revision activation may enqueue every range. Do it before claiming,
        # so initial activation cannot invalidate the claim we are about to use.
        with worker_scope(context):
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
                with worker_execution("record-ranges", claim.token):
                    error: str | None = None
                    try:
                        publication = await self.publish(claim.query, context=context)
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

    @application_operation
    async def publish(self, query: DoorRangeQuery, *, context: ActingContext) -> RangePublication:
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
        publication = await self.get(query_id, context=ActingContext.current())
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
