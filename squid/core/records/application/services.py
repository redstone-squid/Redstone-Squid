"""Record computation orchestration."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import combinations, product
from uuid import UUID

import anyio

from squid.core.actors import ActingContext, application_operation
from squid.core.errors import DataIntegrityError, InvalidStateError
from squid.core.i18n import tr
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD
from squid.core.records.application.affected import ScopeCategories, Threshold
from squid.core.records.application.door_categories import door_category_lookup, family_at_category
from squid.core.records.application.expansion_regions import DoorRangeQuery
from squid.core.records.application.history import reconstruct_history
from squid.core.records.application.live import (
    LiveDoorRecord,
    evaluate_door_record,
    resolve_door_configurations,
)
from squid.core.records.application.models import (
    ALL_RECORDS,
    CandidateBuild,
    CandidateTag,
    CategoryCandidates,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecordResult,
    PublicRecordDetail,
    PublicRecordSummary,
    PublishedRecordResult,
    QueueProcessSummary,
    RebuildSummary,
    RecomputeItem,
    RecomputeTarget,
    RecordDrift,
    RecordGap,
    RecordInspection,
    RecordListFilters,
    RecordListSort,
    RecordLookupRequest,
    RecordNameDiagnostic,
    ScopedCategory,
)
from squid.core.records.application.ports import (
    PublicBuildSummaryReader,
    RecomputeLease,
    RecordQueryUnitOfWork,
    RecordUnitOfWork,
    StoredRecord,
)
from squid.core.records.application.snapshots import RecordSnapshot
from squid.core.records.domain import (
    BuildKind,
    Candidate,
    DoorCategory,
    ExtenderCategory,
    FormattedTitle,
    RecordCriterion,
    RecordResolution,
    RulesTitleFormatter,
    TitleFormatter,
    VersionScope,
)
from squid.core.records.domain.door_evidence import DoorExpansionSource, DoorRecordEvidence
from squid.core.records.domain.models import DOOR_TIMING_METHODS, EXTENDER_TIMING_METHODS, MaterializationSource
from squid.core.records.domain.resolution import resolve_record
from squid.core.records.errors import NoMatchingRecordCategoryError, RecordDefinitionNotFoundError
from squid.core.unit_of_work import UnitOfWorkFactory
from squid.core.worker_context import worker_execution, worker_scope
from squid_i18n import md_tr

_BATCH_SIZE = 200
"""How many queued items of one kind a worker claims and publishes together."""


class RecordAdministrationService:
    """Authorize staff record maintenance: the version pin, rebuilds and the backstop."""

    def __init__(
        self,
        computation: RecordComputationService,
        permissions: PermissionService,
        *,
        uow_factory: UnitOfWorkFactory[RecordUnitOfWork],
    ) -> None:
        self._computation = computation
        self._permissions = permissions
        self._uow_factory = uow_factory

    async def rebuild(
        self,
        *,
        context: ActingContext,
        current_version_id: int | None,
        kinds: Sequence[BuildKind],
    ) -> RebuildSummary:
        """Optionally move the version pin, then recompute every record of `kinds` now."""
        with context.scope():
            await self._permissions.require(context.effective, RECORD_ENTRY_REBUILD)
            if current_version_id is not None:
                async with self._uow_factory() as uow:
                    await uow.records.set_current_version(current_version_id)
                    await uow.commit()
            return await self._computation.rebuild(kinds=kinds, context=context)

    async def request_backstop(self, *, kinds: Sequence[BuildKind], context: ActingContext) -> None:
        """Queue a full recompute of `kinds` that reports every unexplained difference as drift."""
        with context.scope():
            await self._permissions.require(context.effective, RECORD_ENTRY_REBUILD)
            async with self._uow_factory() as uow:
                await uow.records.request_full_recompute(kinds, target=RecomputeTarget.BACKSTOP)
                await uow.commit()


class RecordComputationService:
    """Compare approved builds and publish the records a change affects.

    Categories group builds by features such as door size and restrictions.
    Within each category, the record criterion selects the comparison: first,
    fastest, smallest, or a combination. An all-time scope considers every
    approved candidate; a current scope considers those supporting the pinned
    Minecraft version. A record without competitors has no result: it retires,
    and comes back if a competitor appears.

    Work arrives through the recompute queue, one batch of one kind at a time.
    A batch captures the kind's candidates, works out which categories its
    changes can affect (see `ScopeCategories.affected`), recomputes those, and
    publishes them in one transaction. A full batch recomputes every category;
    it runs after rules, version pin and tag changes, and as a backstop that
    reports drift: differences the affected sets should have caught.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[RecordUnitOfWork],
        formatter: TitleFormatter | None = None,
        category_limit: int = 8,
    ) -> None:
        self._uow_factory = uow_factory
        self._formatter = formatter or RulesTitleFormatter()
        self._category_limit = category_limit
        self._compute_limiter = anyio.CapacityLimiter(1)

    @application_operation
    async def rebuild(
        self, *, kinds: Sequence[BuildKind] = (BuildKind.DOOR, BuildKind.EXTENDER), context: ActingContext
    ) -> RebuildSummary:
        """Recompute and publish every record of `kinds`, without reporting drift."""
        summary = RebuildSummary()
        for kind in kinds:
            snapshot = await self._capture(kind, None)
            batch = await self._compute(snapshot)
            async with self._uow_factory() as uow:
                summary += await uow.records.publish(batch)
                await uow.commit()
        return summary

    async def _capture(self, kind: BuildKind, lease: RecomputeLease | None) -> RecordSnapshot:
        """Read one consistent view of a kind's inputs; without a lease, prepare a full batch."""
        full = lease is None or lease.full
        builds = frozenset() if lease is None else lease.builds
        async with self._uow_factory() as uow:
            await uow.record_candidates.begin_computation()
            revision = await uow.records.active_rules_revision_id()
            pin = await uow.records.active_current_version_id()
            snapshot = RecordSnapshot(
                rules_revision_id=revision,
                kind=kind,
                version_ids=tuple(dict.fromkeys((None, pin))),
                sources=tuple(await uow.record_candidates.list_confirmed(kind)),
                requested=tuple(await uow.records.list_requested_categories(kind)),
                captured_at=datetime.now(UTC),
                input_generation=await uow.records.input_generation(kind),
                full=full,
                changed_builds=builds,
                targets=await uow.records.record_categories(lease.records) if lease is not None else frozenset(),
                prior=await uow.records.prior_publication(kind, builds),
                expected_builds=await uow.records.pending_builds(kind)
                if lease is not None and lease.backstop
                else None,
            )
            await uow.commit()
        return snapshot

    async def _compute(self, snapshot: RecordSnapshot) -> ComputationBatch:
        """Run `compute_snapshot` in a worker thread, one computation at a time."""
        # Non-abandoning cancellation keeps the capacity token until arithmetic
        # finishes, so repeated cancelled requests cannot spawn unbounded work.
        return await anyio.to_thread.run_sync(self.compute_snapshot, snapshot, limiter=self._compute_limiter)

    def compute_snapshot(self, snapshot: RecordSnapshot) -> ComputationBatch:
        """Calculate a batch using only detached in-memory inputs."""
        prior = snapshot.prior
        affected: set[ScopedCategory] = set()
        expected: set[ScopedCategory] | None = set() if snapshot.expected_builds is not None else None
        records: list[ComputedRecordResult] = []
        for version_id in snapshot.version_ids:
            scope = self.scope_categories(snapshot, version_id)
            targets = {key for version, key in snapshot.targets if version == version_id}
            if snapshot.full:
                keys = scope.categories.keys() | {key for version, key in prior.live if version == version_id}
            else:
                keys = scope.affected(snapshot.changed_builds, prior) | targets
            if expected is not None and snapshot.expected_builds is not None:
                expected.update((version_id, key) for key in scope.affected(snapshot.expected_builds, prior) | targets)
            affected.update((version_id, key) for key in keys)
            for key in sorted(keys):
                category = scope.categories.get(key)
                if category is not None:
                    records.extend(self._resolve_category(snapshot, version_id, category))
        if snapshot.full:
            # Only all-time and the pinned version are maintained; other versions retire.
            affected.update(category for category in prior.live if category[0] not in snapshot.version_ids)
        return ComputationBatch(
            rules_revision_id=snapshot.rules_revision_id,
            kind=snapshot.kind,
            input_generation=snapshot.input_generation,
            categories=frozenset(affected),
            records=tuple(records),
            expected=None if expected is None else frozenset(expected),
            full=snapshot.full,
        )

    def scope_categories(self, snapshot: RecordSnapshot, version_id: int | None) -> ScopeCategories:
        """Find every category and its members in one version scope, without resolving records."""
        kind = snapshot.kind
        scoped = tuple(c for c in snapshot.sources if version_id is None or version_id in c.version_ids)
        # Only observed fixed openings seed eager categories. Explicit requests
        # add individual openings; a family's measured example never seeds one.
        fixed = tuple(c for c in scoped if c.expansion is None)
        base_keys = {c.candidate.build_id: _base_key(c) for c in fixed}
        requested_bases = frozenset(identity.base_key for identity in snapshot.requested)
        concrete = set(base_keys.values()) | requested_bases
        projected = tuple(
            projection
            for candidate in scoped
            if candidate.expansion is not None
            for base_key in sorted(concrete)
            if (projection := family_at_category(candidate, CategoryIdentity(kind, base_key, ()), competing_only=True))
            is not None
        )
        contributors = (*fixed, *projected)
        categories = self._eager_categories(kind, contributors)
        for identity in snapshot.requested:
            category = self.requested_category(identity, scoped, competing_only=True)
            if category is not None:
                categories[identity.key] = category
        thresholds: dict[int, frozenset[Threshold]] = {}
        for source in contributors:
            values = frozenset(
                (tag.id, _serialize_category_value(tag.assigned_value))
                for tag in source.restrictions
                if tag.assigned_value is not None and tag.record_operator is not None
            )
            if values:
                build_id = source.candidate.build_id
                thresholds[build_id] = thresholds.get(build_id, frozenset()) | values
        return ScopeCategories(
            version_id=version_id,
            categories=categories,
            thresholds=thresholds,
            base_keys=base_keys,
            requested_bases=requested_bases,
        )

    def _resolve_category(
        self, snapshot: RecordSnapshot, version_id: int | None, category: CategoryCandidates
    ) -> list[ComputedRecordResult]:
        """Resolve each criterion of a category, leaving out criteria without competitors."""
        kind = snapshot.kind
        scope = VersionScope.ALL_TIME if version_id is None else VersionScope.UP_TO_DATE
        ids = {candidate.build_id for candidate in category.candidates}
        sources = tuple(
            source
            for source in snapshot.sources
            if source.candidate.build_id in ids and (version_id is None or version_id in source.version_ids)
        )
        families = tuple(source for source in sources if source.expansion is not None)
        evaluation: LiveDoorRecord | None = None
        records: list[ComputedRecordResult] = []
        for criterion in _base_titles(kind):
            evidence = None
            query = door_category_lookup(category.identity, criterion=criterion, version_id=version_id)
            if families and query is not None:
                evaluation = (
                    evaluate_door_record(query, sources, evaluated_at=snapshot.captured_at)
                    if evaluation is None
                    else resolve_door_configurations(
                        query, evaluation.configurations, evaluated_at=evaluation.evaluated_at
                    )
                )
                resolution = evaluation.resolution
                evidence = DoorRecordEvidence(
                    evaluation=evaluation,
                    sources=tuple(
                        DoorExpansionSource(build_id=s.candidate.build_id, expansion=s.expansion)
                        for s in families
                        if s.expansion is not None
                    ),
                )
            else:
                resolution = _resolve_record(criterion, kind, category.candidates)
            if not resolution:
                continue
            records.append(
                ComputedRecordResult(
                    criterion=criterion,
                    scope=scope,
                    version_id=version_id,
                    category=category,
                    title=self._formatter.format_record(criterion, category.category_text),
                    resolution=resolution,
                    history=()
                    if evidence is not None
                    else reconstruct_history(criterion, _timing_methods(kind), category.candidates),
                    history_complete=evidence is None
                    and all(candidate.completion_at is not None for candidate in category.candidates),
                    door_evidence=evidence,
                )
            )
        return records

    async def process_queue(self, *, context: ActingContext, limit: int = 20) -> QueueProcessSummary:
        """Process up to `limit` batches; publication also acknowledges each batch.

        Each batch holds one kind, and a kind is never claimed by two workers at
        once. A failed batch backs off while other kinds continue. A change
        queued during computation replaces its item's claim token, so the
        acknowledgement leaves it pending.
        """
        with worker_scope(context):
            kinds: list[BuildKind] = []
            summary = RebuildSummary()
            failures: list[Exception] = []
            for _ in range(limit):
                async with self._uow_factory() as uow:
                    lease = await uow.records.claim_recompute(limit=_BATCH_SIZE)
                    await uow.commit()
                if lease.kind is None:
                    break
                with worker_execution("record-maintenance", next(iter(lease.claim_tokens), None)):
                    try:
                        batch = await self._compute(await self._capture(lease.kind, lease))
                        async with self._uow_factory() as uow:
                            summary += await uow.records.publish(batch)
                            await uow.records.complete_recompute(lease)
                            await uow.commit()
                    except Exception as error:
                        async with self._uow_factory() as uow:
                            await uow.records.fail_recompute(lease, str(error))
                            await uow.commit()
                        failures.append(error)
                        continue
                    kinds.append(lease.kind)
            if failures:
                raise failures[0]
            return QueueProcessSummary(kinds=tuple(dict.fromkeys(kinds)), rebuild=summary if kinds else None)

    @application_operation
    async def inspect(self, stored: StoredRecord, *, context: ActingContext) -> RecordInspection:
        """Compute one stored record from current builds, without publishing, and compare the two."""
        record_id = stored.record_id
        lease = RecomputeLease(kind=stored.kind, records=frozenset({record_id}))
        snapshot = await self._capture(stored.kind, lease)
        batch = await self._compute(snapshot)
        category = (stored.version_id, stored.category_key)
        computed = next(
            (
                record
                for record in batch.records
                if (record.version_id, record.category.identity.key) == category
                and record.criterion is stored.criterion
            ),
            None,
        )
        scope = self.scope_categories(snapshot, stored.version_id)
        members = scope.categories.get(stored.category_key)
        member_ids = {candidate.build_id for candidate in members.candidates} if members is not None else set()
        return RecordInspection(
            record_id=record_id,
            title=stored.title,
            published_holder_ids=stored.holder_build_ids,
            computed_holder_ids=computed.resolution.holder_ids if computed is not None else (),
            published_gaps=stored.gaps,
            computed_gaps=computed.resolution.gaps if computed is not None else (),
            members_gained=tuple(sorted(member_ids - set(stored.member_build_ids))),
            members_lost=tuple(sorted(set(stored.member_build_ids) - member_ids)),
        )

    async def queue_backstop(self, *, context: ActingContext) -> None:
        """Queue the scheduled full recompute of every kind that checks the affected sets."""
        with worker_scope(context):
            async with self._uow_factory() as uow:
                await uow.records.request_full_recompute(
                    (BuildKind.DOOR, BuildKind.EXTENDER), target=RecomputeTarget.BACKSTOP
                )
                await uow.commit()

    def _eager_categories(
        self,
        kind: BuildKind,
        candidates: Sequence[CandidateBuild],
    ) -> dict[str, CategoryCandidates]:
        """Generate categories from observed restrictions, bounded by category_limit.

        A build can compete in several categories, including the unrestricted one.
        The limit bounds combinations generated here; explicitly requested exact
        categories are added separately so the limit does not hide those results.
        """
        grouped: dict[str, list[tuple[CandidateBuild, tuple[CandidateTag, ...]]]] = defaultdict(list)
        thresholds = _observed_thresholds(candidates)
        for source in candidates:
            choices = _restriction_choice_groups(source, thresholds)
            for selected in _restriction_combinations(choices, max_size=self._category_limit):
                identity = _category_identity(kind, _base_key(source), selected)
                grouped[identity.key].append((source, selected))
        return {key: self._make_category(items, source=MaterializationSource.EAGER) for key, items in grouped.items()}

    def door_range_candidates(
        self, request: DoorRangeQuery, sources: Sequence[CandidateBuild]
    ) -> tuple[CandidateBuild, ...]:
        """Apply point-lookup tag rules while retaining real opening domains."""
        size = f"{request.minimum}x{request.height}" + (f"x{request.depth}" if request.depth != 1 else "")
        category_sources = tuple(
            replace(source, door=replace(source.door, size=size)) for source in sources if source.door is not None
        )
        category = self.requested_category(
            request.identity_for_opening((request.minimum, request.height, request.depth)), category_sources
        )
        matched_ids = {candidate.build_id for candidate in category.candidates} if category is not None else set()
        return tuple(source for source in sources if source.candidate.build_id in matched_ids)

    def requested_category(
        self,
        identity: CategoryIdentity,
        candidates: Sequence[CandidateBuild],
        *,
        competing_only: bool = False,
    ) -> CategoryCandidates | None:
        """Build the category for one requested exact category, or None if no candidate qualifies.

        With competing_only, families proven absent from the category's opening do
        not qualify; see family_at_category.
        """
        items: list[tuple[CandidateBuild, tuple[CandidateTag, ...]]] = []
        requested_ids = frozenset(identity.restriction_ids)
        requested_values = {tag_id: (operator, value) for tag_id, operator, value in identity.restriction_values}
        for source in candidates:
            if source.expansion is not None:
                projected = family_at_category(source, identity, competing_only=competing_only)
                if projected is None:
                    continue
                source = projected
            restrictions_by_id = {tag.id: tag for tag in source.restrictions}
            if _base_key(source) == identity.base_key and requested_ids <= restrictions_by_id.keys():
                selected: list[CandidateTag] = []
                for tag_id in identity.restriction_ids:
                    tag = restrictions_by_id[tag_id]
                    requested = requested_values.get(tag_id)
                    if requested is None:
                        if tag.assigned_value is not None:
                            break
                        selected.append(tag)
                        continue
                    operator, raw_value = requested
                    threshold = _coerce_category_value(tag, raw_value)
                    if threshold is None or tag.record_operator != operator or not _satisfies(tag, threshold):
                        break
                    selected.append(replace(tag, category_value=threshold))
                else:
                    items.append((source, tuple(selected)))
        return self._make_category(items, source=MaterializationSource.PUBLIC_LOOKUP) if items else None

    def _make_category(
        self,
        items: Sequence[tuple[CandidateBuild, tuple[CandidateTag, ...]]],
        *,
        source: str,
    ) -> CategoryCandidates:
        first, restrictions = items[0]
        category = _category_with_restrictions(first, restrictions)
        category_text = (
            self._formatter.format_door(category)
            if isinstance(category, DoorCategory)
            else self._formatter.format_extender(category)
        )
        identity = _category_identity(first.kind, _base_key(first), restrictions)
        tags = tuple(sorted((*first.types, *restrictions), key=lambda tag: (tag.kind, tag.id)))
        return CategoryCandidates(
            identity=identity,
            tags=tags,
            category_text=category_text,
            candidates=tuple(item[0].candidate for item in items),
            source=MaterializationSource.PUBLIC_LOOKUP if source == "public_lookup" else MaterializationSource.EAGER,
        )

    def format_record_titles(self, category: CategoryCandidates) -> dict[RecordCriterion, FormattedTitle]:
        """Format a record name for each criterion in this category."""
        return {
            criterion: self._formatter.format_record(criterion, category.category_text)
            for criterion in _base_titles(category.identity.kind)
        }


class RecordService:
    """Query published records, add exact categories, and inspect or requeue records.

    Automatic computation bounds the number of restriction combinations it
    generates. A lookup can request a more specific category; if a confirmed
    build qualifies, the service saves it and queues its records.
    """

    def __init__(
        self,
        computation: RecordComputationService,
        *,
        permissions: PermissionService,
        uow_factory: UnitOfWorkFactory[RecordUnitOfWork],
    ) -> None:
        self._uow_factory = uow_factory
        self._computation = computation
        self._permissions = permissions

    @application_operation
    async def gaps(self, *, kind: BuildKind | None = None, context: ActingContext) -> Sequence[RecordGap]:
        """Return decisive facts missing from current record results."""
        await self._permissions.require(context.effective, RECORD_ENTRY_INSPECT)
        async with self._uow_factory() as uow:
            return await uow.records.list_gaps(kind=kind)

    @application_operation
    async def title_gaps(
        self, *, kind: BuildKind | None = None, context: ActingContext
    ) -> Sequence[RecordNameDiagnostic]:
        """Return current record names requiring tag review."""
        await self._permissions.require(context.effective, RECORD_ENTRY_INSPECT)
        async with self._uow_factory() as uow:
            return await uow.records.list_title_gaps(kind=kind)

    @application_operation
    async def get(self, result_id: int, *, context: ActingContext) -> PublishedRecordResult | None:
        """Return one current record result."""
        async with self._uow_factory() as uow:
            return await uow.records.get_published_record(result_id)

    @application_operation
    async def request_recompute(self, record_id: UUID, *, context: ActingContext) -> bool:
        """Queue every criterion of a record's category; return False for an unknown record."""
        await self._permissions.require(context.effective, RECORD_ENTRY_INSPECT)
        async with self._uow_factory() as uow:
            queued = await uow.records.request_record_recompute(record_id)
            await uow.commit()
        return queued

    @application_operation
    async def request_definition_recompute(self, definition_id: int, *, context: ActingContext) -> None:
        """Queue the record a definition names, as diagnostics identify records by definition."""
        await self._permissions.require(context.effective, RECORD_ENTRY_INSPECT)
        async with self._uow_factory() as uow:
            record_id = await uow.records.definition_record_id(definition_id)
            if record_id is None:
                raise RecordDefinitionNotFoundError(definition_id)
            await uow.records.request_record_recompute(record_id)
            await uow.commit()

    @application_operation
    async def lookup_or_materialize(self, request: RecordLookupRequest, *, context: ActingContext) -> int:
        """Save a valid exact category and queue its records; return how many were queued.

        A saved category is kept in every maintained version scope, so it needs a
        qualifying build in some version but not in each one.
        """
        await self._permissions.require(context.effective, RECORD_ENTRY_INSPECT)
        async with self._uow_factory() as uow:
            await uow.record_candidates.begin_computation()
            source = tuple(await uow.record_candidates.list_confirmed(request.kind))
            identity = CategoryIdentity(
                request.kind,
                request.base_key,
                tuple(sorted(request.restriction_ids)),
                tuple(sorted(request.restriction_values)),
            )
            category = self._computation.requested_category(identity, source, competing_only=True)
            if category is None:
                raise NoMatchingRecordCategoryError(kind=request.kind.value, base_key=request.base_key)
            rules_revision_id = await uow.records.active_rules_revision_id()
            queued = await uow.records.save_requested_category(
                rules_revision_id, identity, self._computation.format_record_titles(category)
            )
            await uow.commit()
        return queued

    @application_operation
    async def inspect(self, record_id: UUID, *, context: ActingContext) -> RecordInspection | None:
        """Compute one record from current builds and compare it with what is published.

        Nothing is published. The comparison covers holders, gaps and the members
        of the record's category.
        """
        await self._permissions.require(context.effective, RECORD_ENTRY_INSPECT)
        async with self._uow_factory() as uow:
            stored = await uow.records.stored_record(record_id)
        if stored is None:
            return None
        return await self._computation.inspect(stored, context=context)

    @application_operation
    async def recompute_queue(self, *, context: ActingContext) -> Sequence[RecomputeItem]:
        """Return pending and failed recompute requests, oldest first."""
        await self._permissions.require(context.effective, RECORD_ENTRY_INSPECT)
        async with self._uow_factory() as uow:
            return await uow.records.list_recompute_queue()

    @application_operation
    async def drift(
        self, *, selector: PageSelector = FIRST_PAGE, page_size: int = 20, context: ActingContext
    ) -> Page[RecordDrift]:
        """Return backstop differences, newest first."""
        await self._permissions.require(context.effective, RECORD_ENTRY_INSPECT)
        async with self._uow_factory() as uow:
            return await uow.records.list_drift(selector=selector, page_size=page_size)


class PublicRecordQueryService:
    """Combine records with their holders' public build summaries."""

    def __init__(
        self, builds: PublicBuildSummaryReader, *, uow_factory: UnitOfWorkFactory[RecordQueryUnitOfWork]
    ) -> None:
        self._uow_factory = uow_factory
        self._builds = builds

    async def list_page(
        self,
        *,
        selector: PageSelector = FIRST_PAGE,
        sort: RecordListSort = RecordListSort.ID,
        filters: RecordListFilters = ALL_RECORDS,
        page_size: int = 20,
        context: ActingContext,
    ) -> Page[PublicRecordSummary]:
        """Return one page of current results in display order, with one summary load for the page."""
        async with self._uow_factory() as uow:
            page = await uow.records.list_published_records(
                selector=selector, sort=sort, filters=filters, page_size=page_size
            )
        holder_ids = tuple(dict.fromkeys(build_id for record in page.items for build_id in record.holder_build_ids))
        builds = {build.id: build for build in await self._builds.get_public_summaries(holder_ids, context=context)}
        items = tuple(PublicRecordSummary.with_builds(record, builds) for record in page.items)
        return Page(items, page.total, page.next, page.prev)

    async def get(self, record_id: UUID, *, context: ActingContext) -> PublicRecordDetail | None:
        """Return a record's current result, or its retired state, with the builds it names.

        Raise `DataIntegrityError` when a current result has no holders or any
        holder is unavailable in the public catalogue.
        """
        async with self._uow_factory() as uow:
            result = await uow.records.get_latest_record(record_id)
            if result is None:
                return None
            history = await uow.records.get_record_history(record_id)
            siblings = await uow.records.list_siblings(record_id)
        if result.result_id is not None and not result.holder_build_ids:
            msg = "A current record result has no holders."
            raise DataIntegrityError(msg, context={"record_id": str(record_id), "result_id": result.result_id})
        gap_ids = dict.fromkeys(gap.build_id for gap in result.gaps)
        history_ids = dict.fromkeys(build_id for period in history for build_id in period.build_ids)
        named_ids = tuple(dict.fromkeys((*result.holder_build_ids, *result.tied_build_ids, *gap_ids, *history_ids)))
        found = await self._builds.get_public_summaries(named_ids, context=context)
        by_id = {build.id: build for build in found}
        unavailable_ids = [build_id for build_id in result.holder_build_ids if build_id not in by_id]
        if unavailable_ids:
            msg = "A published record references holder builds that are unavailable to the public catalogue."
            raise DataIntegrityError(
                msg,
                context={"record_id": str(record_id), "unavailable_holder_build_ids": unavailable_ids},
            )
        return PublicRecordDetail.with_builds(result, by_id, history, siblings)


def _observed_thresholds(
    candidates: Sequence[CandidateBuild],
) -> dict[int, tuple[Decimal | str | bool, ...]]:
    values: dict[int, set[Decimal | str | bool]] = defaultdict(set)
    for source in candidates:
        for tag in source.restrictions:
            if tag.assigned_value is not None and tag.record_operator is not None:
                values[tag.id].add(tag.assigned_value)
    return {
        tag_id: tuple(sorted(tag_values, key=lambda value: (type(value).__name__, str(value))))
        for tag_id, tag_values in values.items()
    }


def _restriction_choice_groups(
    source: CandidateBuild,
    thresholds: dict[int, tuple[Decimal | str | bool, ...]],
) -> tuple[tuple[CandidateTag, ...], ...]:
    groups: list[tuple[CandidateTag, ...]] = []
    for tag in sorted(source.restrictions, key=lambda item: item.id):
        if tag.assigned_value is None:
            groups.append((tag,))
            continue
        if tag.record_operator is None:
            continue
        eligible = tuple(
            replace(tag, category_value=threshold)
            for threshold in thresholds.get(tag.id, ())
            if _satisfies(tag, threshold)
        )
        if eligible:
            groups.append(eligible)
    return tuple(groups)


def _restriction_combinations(
    groups: tuple[tuple[CandidateTag, ...], ...],
    *,
    max_size: int,
) -> Iterable[tuple[CandidateTag, ...]]:
    """Choose at most one threshold per restriction, up to `max_size` restrictions."""
    yield ()
    for size in range(1, min(max_size, len(groups)) + 1):
        for selected_groups in combinations(groups, size):
            yield from product(*selected_groups)


def _satisfies(tag: CandidateTag, threshold: Decimal | str | bool) -> bool:
    assigned = tag.assigned_value
    if assigned is None:
        return False
    if tag.record_operator == "exact":
        return assigned == threshold
    if not isinstance(assigned, Decimal) or not isinstance(threshold, Decimal):
        return False
    if tag.record_operator == "at_most":
        return assigned <= threshold
    if tag.record_operator == "at_least":
        return assigned >= threshold
    return False


def _category_identity(
    kind: BuildKind,
    base_key: str,
    restrictions: Sequence[CandidateTag],
) -> CategoryIdentity:
    values = tuple(
        sorted(
            (
                tag.id,
                tag.record_operator or "exact",
                _serialize_category_value(tag.category_value),
            )
            for tag in restrictions
            if tag.category_value is not None
        )
    )
    return CategoryIdentity(kind, base_key, tuple(sorted(tag.id for tag in restrictions)), values)


def _serialize_category_value(value: Decimal | str | bool | None) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return value
    msg = tr(
        t"records-parameterized-record-restriction = Parameterized record restriction is missing a category value."
    )
    raise DataIntegrityError(msg)


def _coerce_category_value(tag: CandidateTag, value: str) -> Decimal | str | bool | None:
    # An incompatible threshold type cannot match this tag. In particular,
    # nonfinite decimals must never reach ordered comparisons.
    if tag.value_type == "numeric":
        try:
            number = Decimal(value)
        except InvalidOperation:
            return None
        # Bound rendered thresholds as well as input length: a short exponent
        # such as 1e999999 must not produce an enormous category title.
        return number if number.is_finite() and -128 <= number.adjusted() <= 128 else None
    if tag.value_type == "boolean":
        return value == "true" if value in {"true", "false"} else None
    return value


def _base_key(source: CandidateBuild) -> str:
    type_key = ",".join(str(tag.id) for tag in sorted(source.types, key=lambda tag: tag.id))
    if source.door is not None:
        door = source.door
        return "|".join(("door", door.size, f"t[{type_key}]", door.orientation))
    if source.extender is not None:
        extender = source.extender
        return "|".join(
            (
                "extender",
                extender.orientation,
                str(extender.length),
                f"t[{type_key or ','.join(extender.types)}]",
            )
        )
    build_id = source.candidate.build_id
    raise DataIntegrityError(
        md_tr(
            t"records-candidate-no-typed = Candidate {build_id!s} has no typed category facts.",
        )
    )


def _category_with_restrictions(
    source: CandidateBuild,
    restrictions: Sequence[CandidateTag],
) -> DoorCategory | ExtenderCategory:
    wiring = tuple(tag.category_name for tag in restrictions if tag.restriction_type == "wiring-placement")
    animated = tuple(tag.category_name for tag in restrictions if tag.restriction_type == "animated")
    components = tuple(tag.category_name for tag in restrictions if tag.restriction_type == "component")
    miscellaneous = tuple(tag.category_name for tag in restrictions if tag.restriction_type == "miscellaneous")
    if source.door is not None:
        return replace(
            source.door,
            wiring_restrictions=wiring,
            animated_restrictions=animated,
            component_restrictions=components,
            miscellaneous_restrictions=miscellaneous,
        )
    if source.extender is not None:
        return replace(
            source.extender,
            wiring_restrictions=wiring,
            component_restrictions=components,
            miscellaneous_restrictions=miscellaneous,
        )
    build_id = source.candidate.build_id
    raise DataIntegrityError(
        md_tr(
            t"records-candidate-no-typed = Candidate {build_id!s} has no typed category facts.",
        )
    )


def _timing_methods(kind: BuildKind) -> tuple[str, ...]:
    if kind is BuildKind.DOOR:
        return tuple(DOOR_TIMING_METHODS)
    if kind is BuildKind.EXTENDER:
        return tuple(EXTENDER_TIMING_METHODS)
    kind_name = kind.value
    raise InvalidStateError(
        md_tr(
            t"records-fastest-records-unsupported = Fastest records are not supported for {kind_name}.",
        )
    )


def _base_titles(kind: BuildKind) -> tuple[RecordCriterion, ...]:
    shared = (
        RecordCriterion.FASTEST,
        RecordCriterion.SMALLEST,
        RecordCriterion.FASTEST_SMALLEST,
        RecordCriterion.SMALLEST_FASTEST,
    )
    if kind is BuildKind.DOOR:
        return (RecordCriterion.FIRST, *shared)
    if kind is BuildKind.EXTENDER:
        return shared
    kind_name = kind.value
    raise InvalidStateError(md_tr(t"records-records-not-supported = Records are not supported for {kind_name}."))


def _resolve_record(
    criterion: RecordCriterion,
    kind: BuildKind,
    candidates: Iterable[Candidate],
) -> RecordResolution:
    return resolve_record(criterion, candidates, _timing_methods(kind))
