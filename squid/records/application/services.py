"""Record computation orchestration."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import combinations, product

import anyio

from squid.core.errors import DataIntegrityError, InvalidStateError, ServiceUnavailableError, ValidationError
from squid.core.i18n import tr
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page
from squid.records.application.door_categories import door_category_lookup, family_at_category
from squid.records.application.expansion_regions import DoorRangeQuery, DoorRangeResult
from squid.records.application.history import reconstruct_history
from squid.records.application.live import (
    DoorRecordLookup,
    LiveDoorRecord,
    evaluate_door_record,
    resolve_door_configurations,
)
from squid.records.application.models import (
    CandidateBuild,
    CandidateFacet,
    CategoryCandidates,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecord,
    PublicRecordDetail,
    PublishedTitle,
    QueueProcessSummary,
    RebuildSummary,
    RecordGap,
    RecordLookupRequest,
    TitleDiagnosticGap,
)
from squid.records.application.ports import (
    DoorRangeAnalyzer,
    PublicBuildSummaryReader,
    RecordQueryUnitOfWork,
    RecordUnitOfWork,
)
from squid.records.application.snapshots import RecordSnapshot
from squid.records.domain import (
    BaseTitle,
    BuildKind,
    Candidate,
    DoorCategory,
    ExtenderCategory,
    FormattedTitle,
    ResolutionStatus,
    RulesTitleFormatter,
    TitleFormatter,
    TitleResolution,
    VersionScope,
)
from squid.records.domain.door_evidence import DoorExpansionSource, DoorRecordEvidence
from squid.records.domain.models import DOOR_TIMING_METHODS, EXTENDER_TIMING_METHODS, MaterializationSource
from squid.records.domain.resolution import resolve_record
from squid.records.errors import NoMatchingRecordCategoryError, RecordDefinitionNotFoundError
from squid.unit_of_work import UnitOfWorkFactory
from squid_i18n import md_tr


class RecordComputationService:
    """Compare confirmed builds and publish replacement record results.

    Categories group builds by features such as door size and restrictions.
    Within each category, the base title selects the comparison: first,
    fastest, smallest, or a combination. An all-time scope considers every
    confirmed candidate; a current scope considers those supporting the pinned
    Minecraft version. Resolution can retain tied holders or report missing
    facts that prevent a decision.

    A rebuild computes all results for each requested kind and scope, then
    activates the replacement runs together. Readers continue to see the old
    results until that transaction commits.
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

    async def rebuild(
        self,
        *,
        current_version_id: int | None = None,
        kinds: Sequence[BuildKind] = (BuildKind.DOOR, BuildKind.EXTENDER),
    ) -> RebuildSummary:
        """Publish concrete-title runs together; range work has its own queue."""
        async with self._uow_factory() as uow:
            await uow.record_candidates.begin_computation()
            result = await self.rebuild_in(uow, current_version_id=current_version_id, kinds=kinds)
            await uow.commit()
        return result

    async def rebuild_in(
        self,
        uow: RecordUnitOfWork,
        *,
        current_version_id: int | None = None,
        kinds: Sequence[BuildKind] = (BuildKind.DOOR, BuildKind.EXTENDER),
    ) -> RebuildSummary:
        """Stage replacement runs in the caller's transaction without committing.

        Every kind gets an all-time run. A supplied current version also gets a
        filtered run. The caller must pin a consistent snapshot before reading
        or writing computation inputs.
        """
        rules_revision_id = await uow.records.active_rules_revision_id()
        run_ids: list[int] = []
        records: list[ComputedRecord] = []
        for kind in kinds:
            source = tuple(await uow.record_candidates.list_confirmed(kind))
            requested = tuple(await uow.records.list_requested_categories(kind))
            snapshot = RecordSnapshot(
                rules_revision_id=rules_revision_id,
                kind=kind,
                sources=source,
                version_id=None,
                requested=requested,
                captured_at=datetime.now(UTC),
            )
            all_time = await self._compute_snapshot(snapshot)
            run_ids.append(await uow.records.activate(all_time))
            records.extend(all_time.records)

            if current_version_id is not None:
                current = await self._compute_snapshot(snapshot.model_copy(update={"version_id": current_version_id}))
                run_ids.append(await uow.records.activate(current))
                records.extend(current.records)

        return RebuildSummary(
            run_ids=tuple(run_ids),
            definitions=len(records),
            resolved=sum(record.resolution.status is ResolutionStatus.RESOLVED for record in records),
            unresolved=sum(record.resolution.status is ResolutionStatus.UNRESOLVED for record in records),
        )

    async def _compute_snapshot(self, snapshot: RecordSnapshot) -> ComputationBatch:
        # Non-abandoning cancellation keeps the capacity token until arithmetic
        # finishes, so repeated cancelled requests cannot spawn unbounded work.
        return await anyio.to_thread.run_sync(self.compute_snapshot, snapshot, limiter=self._compute_limiter)

    def compute_snapshot(self, snapshot: RecordSnapshot) -> ComputationBatch:
        """Calculate one scope using only detached, replayable inputs."""
        rules_revision_id = snapshot.rules_revision_id
        kind = snapshot.kind
        source = snapshot.sources
        version_id = snapshot.version_id
        requested = snapshot.requested
        scoped = tuple(candidate for candidate in source if version_id is None or version_id in candidate.version_ids)
        # Only observed fixed openings seed eager categories. Explicit requests
        # add individual openings; a family's measured example never seeds one.
        fixed = tuple(c for c in scoped if c.expansion is None)
        concrete = {_base_key(c) for c in fixed} | {identity.base_key for identity in requested}
        projected = tuple(
            projection
            for candidate in scoped
            if candidate.expansion is not None
            for base_key in sorted(concrete)
            if (projection := family_at_category(candidate, CategoryIdentity(kind, base_key, ()), competing_only=True))
            is not None
        )
        categories = self._eager_categories(kind, (*fixed, *projected))
        for identity in requested:
            category = self.requested_category(identity, scoped, competing_only=True)
            if category is not None:
                categories[identity.key] = category

        scope = VersionScope.ALL_TIME if version_id is None else VersionScope.UP_TO_DATE
        records: list[ComputedRecord] = []
        for category in categories.values():
            ids = {candidate.build_id for candidate in category.candidates}
            sources = tuple(source for source in scoped if source.candidate.build_id in ids)
            families = tuple(source for source in sources if source.expansion is not None)
            evaluation: LiveDoorRecord | None = None
            for base_title in _base_titles(kind):
                evidence = None
                query = door_category_lookup(category.identity, base_title=base_title, version_id=version_id)
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
                    resolution = _resolve_record(base_title, kind, category.candidates)
                records.append(
                    ComputedRecord(
                        base_title=base_title,
                        scope=scope,
                        version_id=version_id,
                        category=category,
                        title=self._formatter.format_record(base_title, category.category_text),
                        resolution=resolution,
                        history=()
                        if evidence is not None
                        else reconstruct_history(base_title, _timing_methods(kind), category.candidates),
                        history_complete=evidence is None
                        and all(candidate.completion_at is not None for candidate in category.candidates),
                        door_evidence=evidence,
                    )
                )
        return ComputationBatch(
            rules_revision_id=rules_revision_id,
            kind=kind,
            version_id=version_id,
            records=tuple(records),
        )

    async def process_queue(
        self,
        *,
        current_version_id: int | None = None,
        limit: int = 20,
    ) -> QueueProcessSummary:
        """Claim scopes, rebuild their kinds, then acknowledge the owned claims.

        Claiming, publication, and acknowledgement use separate transactions;
        claim locks are released before rebuilding starts. An omitted version
        reuses the current pin. Rebuild failures release owned claims for retry
        and propagate.
        """
        async with self._uow_factory() as uow:
            lease = await uow.records.claim_recompute_kinds(limit=limit)
            if current_version_id is None:
                current_version_id = await uow.records.active_current_version_id()
            await uow.commit()
        if not lease:
            return QueueProcessSummary(kinds=(), rebuild=None)
        try:
            summary = await self.rebuild(current_version_id=current_version_id, kinds=lease.kinds)
        except Exception as error:
            async with self._uow_factory() as uow:
                await uow.records.fail_recompute(lease, str(error))
                await uow.commit()
            raise
        async with self._uow_factory() as uow:
            await uow.records.complete_recompute(lease)
            await uow.commit()
        return QueueProcessSummary(kinds=lease.kinds, rebuild=summary)

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
        grouped: dict[str, list[tuple[CandidateBuild, tuple[CandidateFacet, ...]]]] = defaultdict(list)
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
        """Apply point-lookup taxonomy rules while retaining real opening domains."""
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
        items: list[tuple[CandidateBuild, tuple[CandidateFacet, ...]]] = []
        requested_ids = frozenset(identity.restriction_ids)
        requested_values = {tag_id: (operator, value) for tag_id, operator, value in identity.restriction_values}
        for source in candidates:
            if source.expansion is not None:
                projected = family_at_category(source, identity, competing_only=competing_only)
                if projected is None:
                    continue
                source = projected
            restrictions_by_id = {facet.id: facet for facet in source.restrictions}
            if _base_key(source) == identity.base_key and requested_ids <= restrictions_by_id.keys():
                selected: list[CandidateFacet] = []
                for facet_id in identity.restriction_ids:
                    facet = restrictions_by_id[facet_id]
                    requested = requested_values.get(facet_id)
                    if requested is None:
                        if facet.assigned_value is not None:
                            break
                        selected.append(facet)
                        continue
                    operator, raw_value = requested
                    threshold = _coerce_category_value(facet, raw_value)
                    if threshold is None or facet.record_operator != operator or not _satisfies(facet, threshold):
                        break
                    selected.append(replace(facet, category_value=threshold))
                else:
                    items.append((source, tuple(selected)))
        return self._make_category(items, source=MaterializationSource.PUBLIC_LOOKUP) if items else None

    def _make_category(
        self,
        items: Sequence[tuple[CandidateBuild, tuple[CandidateFacet, ...]]],
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
        facets = tuple(sorted((*first.types, *restrictions), key=lambda facet: (facet.kind, facet.id)))
        return CategoryCandidates(
            identity=identity,
            facets=facets,
            category_text=category_text,
            candidates=tuple(item[0].candidate for item in items),
            source=MaterializationSource.PUBLIC_LOOKUP if source == "public_lookup" else MaterializationSource.EAGER,
        )

    def format_record_titles(self, category: CategoryCandidates) -> dict[BaseTitle, FormattedTitle]:
        """Render the base titles a definition stores for this category."""
        return {
            base_title: self._formatter.format_record(base_title, category.category_text)
            for base_title in _base_titles(category.identity.kind)
        }


class RecordService:
    """Query published results and add exact categories requested by users.

    Automatic computation bounds the number of restriction combinations it
    generates. A lookup can request a more specific category; if a confirmed
    build qualifies, the service saves it for future rebuilds and recomputes
    that build kind before returning.
    """

    def __init__(
        self,
        computation: RecordComputationService,
        *,
        uow_factory: UnitOfWorkFactory[RecordUnitOfWork],
        range_analyzer: DoorRangeAnalyzer | None = None,
        range_concurrency: int = 2,
    ) -> None:
        self._uow_factory = uow_factory
        self._computation = computation
        self._range_analyzer = range_analyzer
        self._range_limiter = anyio.CapacityLimiter(range_concurrency)

    async def gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        """Return decisive facts missing from active record results."""
        async with self._uow_factory() as uow:
            return await uow.records.list_gaps(kind=kind)

    async def title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]:
        """Return active canonical record titles requiring taxonomy review."""
        async with self._uow_factory() as uow:
            return await uow.records.list_title_gaps(kind=kind)

    async def get(self, result_id: int) -> PublishedTitle | None:
        """Return one published computed record result."""
        async with self._uow_factory() as uow:
            return await uow.records.get_published_record(result_id)

    async def list_page(
        self,
        *,
        selector: PageSelector = FIRST_PAGE,
        descending: bool = True,
        page_size: int = 20,
        resolved_only: bool = False,
    ) -> Page[PublishedTitle]:
        """Return one page of published record results in display order."""
        async with self._uow_factory() as uow:
            rows = await uow.records.list_published_records(
                offset=selector.offset,
                after_id=selector.after_id,
                before_id=selector.before_id,
                descending=descending,
                # One row past the page proves whether another page follows.
                limit=page_size + 1,
                resolved_only=resolved_only,
            )
            return keyset_page(
                rows,
                selector=selector,
                page_size=page_size,
                total=await uow.records.count_published_records(resolved_only=resolved_only),
                keyset=True,
                id_of=lambda record: record.id,
            )

    async def lookup_door(self, request: DoorRecordLookup) -> LiveDoorRecord:
        """Evaluate one size against current confirmed facts without persisting it.

        The repository pins a read-only database snapshot before loading related
        facts. Evaluation continues on immutable values after releasing that
        transaction. This result does not replace published record runs.
        """
        async with self._uow_factory() as uow:
            sources = tuple(
                await uow.record_candidates.list_for_door_lookup(
                    request.opening, request.orientation, request.version_id
                )
            )
        category = self._computation.requested_category(request.identity, sources)
        matched_ids = {candidate.build_id for candidate in category.candidates} if category is not None else set()
        return evaluate_door_record(
            request,
            tuple(source for source in sources if source.candidate.build_id in matched_ids),
            evaluated_at=datetime.now(UTC),
        )

    async def lookup_door_ranges(self, request: DoorRangeQuery) -> DoorRangeResult:
        """Analyze live smallest-record widths without publishing or storing regions.

        Admission covers database loading and analysis. The worker receives
        immutable facts after the read-only transaction closes. Saturation fails
        immediately; requests do not queue with loaded candidate documents.
        """
        if self._range_analyzer is None:
            raise ServiceUnavailableError(context={"reason": "range_analyzer_unavailable"})
        try:
            self._range_limiter.acquire_nowait()
        except anyio.WouldBlock as error:
            raise ServiceUnavailableError(context={"reason": "range_capacity"}) from error
        try:
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
            result = await self._range_analyzer.analyze(
                self._computation.door_range_candidates(request, sources), request
            )
            return DoorRangeResult(request, datetime.now(UTC), result.analysis, result.configurations)
        finally:
            self._range_limiter.release()

    async def materialize_definition(
        self,
        definition_id: int,
        *,
        kind: BuildKind | None = None,
        version_id: int | None = None,
    ) -> RebuildSummary:
        """Re-materialize the exact category an existing definition identifies."""
        async with self._uow_factory() as uow:
            identity = await uow.records.get_definition_identity(definition_id)
        if identity is None:
            raise RecordDefinitionNotFoundError(definition_id)
        if kind is not None and kind is not identity.kind:
            actual = identity.kind.value
            requested = kind.value
            raise ValidationError(
                md_tr(
                    t"records-record-category-category = Record category {definition_id!s} is "
                    t"a {actual} category, not {requested}.",
                )
            )
        return await self.lookup_or_materialize(
            RecordLookupRequest(
                kind=identity.kind,
                base_key=identity.base_key,
                restriction_ids=frozenset(identity.restriction_ids),
                restriction_values=identity.restriction_values,
                version_id=version_id,
            )
        )

    async def lookup_or_materialize(self, request: RecordLookupRequest) -> RebuildSummary:
        """Persist a valid exact category and refresh its build kind."""
        async with self._uow_factory() as uow:
            await uow.record_candidates.begin_computation()
            source = tuple(await uow.record_candidates.list_confirmed(request.kind))
            scoped = tuple(
                candidate
                for candidate in source
                if request.version_id is None or request.version_id in candidate.version_ids
            )
            identity = CategoryIdentity(
                request.kind,
                request.base_key,
                tuple(sorted(request.restriction_ids)),
                tuple(sorted(request.restriction_values)),
            )
            category = self._computation.requested_category(identity, scoped, competing_only=True)
            if category is None:
                raise NoMatchingRecordCategoryError(kind=request.kind.value, base_key=request.base_key)

            rules_revision_id = await uow.records.active_rules_revision_id()
            await uow.records.save_requested_category(
                rules_revision_id, identity, self._computation.format_record_titles(category)
            )
            result = await self._computation.rebuild_in(
                uow,
                current_version_id=request.version_id,
                kinds=(request.kind,),
            )
            await uow.commit()
            return result


class PublicRecordQueryService:
    """Combine published results with all their holders' public build summaries."""

    def __init__(
        self, builds: PublicBuildSummaryReader, *, uow_factory: UnitOfWorkFactory[RecordQueryUnitOfWork]
    ) -> None:
        self._uow_factory = uow_factory
        self._builds = builds

    async def get(self, result_id: int) -> PublicRecordDetail | None:
        """Return an active result with holders in result order, or `None` if absent.

        Raise `DataIntegrityError` when holder rows contradict the result status
        or any referenced build is unavailable in the public catalogue.
        """
        async with self._uow_factory() as uow:
            result = await uow.records.get_published_record(result_id)
        if result is None:
            return None
        holders_match_status = bool(result.holder_build_ids) is (result.status is ResolutionStatus.RESOLVED)
        if not holders_match_status:
            msg = "A published record's result status contradicts its holder rows."
            raise DataIntegrityError(
                msg,
                context={
                    "record_id": result.id,
                    "status": result.status.value,
                    "holder_build_ids": list(result.holder_build_ids),
                },
            )
        found = await self._builds.get_public_summaries(result.holder_build_ids)
        by_id = {build.id: build for build in found}
        unavailable_ids = [build_id for build_id in result.holder_build_ids if build_id not in by_id]
        if unavailable_ids:
            msg = "A published record references holder builds that are unavailable to the public catalogue."
            raise DataIntegrityError(
                msg,
                context={"record_id": result.id, "unavailable_holder_build_ids": unavailable_ids},
            )
        return PublicRecordDetail.with_holders(result, [by_id[build_id] for build_id in result.holder_build_ids])


def _observed_thresholds(
    candidates: Sequence[CandidateBuild],
) -> dict[int, tuple[Decimal | str | bool, ...]]:
    values: dict[int, set[Decimal | str | bool]] = defaultdict(set)
    for source in candidates:
        for facet in source.restrictions:
            if facet.assigned_value is not None and facet.record_operator is not None:
                values[facet.id].add(facet.assigned_value)
    return {
        tag_id: tuple(sorted(tag_values, key=lambda value: (type(value).__name__, str(value))))
        for tag_id, tag_values in values.items()
    }


def _restriction_choice_groups(
    source: CandidateBuild,
    thresholds: dict[int, tuple[Decimal | str | bool, ...]],
) -> tuple[tuple[CandidateFacet, ...], ...]:
    groups: list[tuple[CandidateFacet, ...]] = []
    for facet in sorted(source.restrictions, key=lambda item: item.id):
        if facet.assigned_value is None:
            groups.append((facet,))
            continue
        if facet.record_operator is None:
            continue
        eligible = tuple(
            replace(facet, category_value=threshold)
            for threshold in thresholds.get(facet.id, ())
            if _satisfies(facet, threshold)
        )
        if eligible:
            groups.append(eligible)
    return tuple(groups)


def _restriction_combinations(
    groups: tuple[tuple[CandidateFacet, ...], ...],
    *,
    max_size: int,
) -> Iterable[tuple[CandidateFacet, ...]]:
    """Choose at most one threshold per restriction, up to `max_size` restrictions."""
    yield ()
    for size in range(1, min(max_size, len(groups)) + 1):
        for selected_groups in combinations(groups, size):
            yield from product(*selected_groups)


def _satisfies(facet: CandidateFacet, threshold: Decimal | str | bool) -> bool:
    assigned = facet.assigned_value
    if assigned is None:
        return False
    if facet.record_operator == "exact":
        return assigned == threshold
    if not isinstance(assigned, Decimal) or not isinstance(threshold, Decimal):
        return False
    if facet.record_operator == "at_most":
        return assigned <= threshold
    if facet.record_operator == "at_least":
        return assigned >= threshold
    return False


def _category_identity(
    kind: BuildKind,
    base_key: str,
    restrictions: Sequence[CandidateFacet],
) -> CategoryIdentity:
    values = tuple(
        sorted(
            (
                facet.id,
                facet.record_operator or "exact",
                _serialize_category_value(facet.category_value),
            )
            for facet in restrictions
            if facet.category_value is not None
        )
    )
    return CategoryIdentity(kind, base_key, tuple(sorted(facet.id for facet in restrictions)), values)


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


def _coerce_category_value(facet: CandidateFacet, value: str) -> Decimal | str | bool | None:
    # An incompatible threshold type cannot match this facet. In particular,
    # nonfinite decimals must never reach ordered comparisons.
    if facet.value_type == "numeric":
        try:
            number = Decimal(value)
        except InvalidOperation:
            return None
        # Bound rendered thresholds as well as input length: a short exponent
        # such as 1e999999 must not produce an enormous category title.
        return number if number.is_finite() and -128 <= number.adjusted() <= 128 else None
    if facet.value_type == "boolean":
        return value == "true" if value in {"true", "false"} else None
    return value


def _base_key(source: CandidateBuild) -> str:
    type_key = ",".join(str(facet.id) for facet in sorted(source.types, key=lambda facet: facet.id))
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
    restrictions: Sequence[CandidateFacet],
) -> DoorCategory | ExtenderCategory:
    wiring = tuple(facet.category_name for facet in restrictions if facet.restriction_type == "wiring-placement")
    animated = tuple(facet.category_name for facet in restrictions if facet.restriction_type == "animated")
    components = tuple(facet.category_name for facet in restrictions if facet.restriction_type == "component")
    miscellaneous = tuple(facet.category_name for facet in restrictions if facet.restriction_type == "miscellaneous")
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


def _base_titles(kind: BuildKind) -> tuple[BaseTitle, ...]:
    shared = (
        BaseTitle.FASTEST,
        BaseTitle.SMALLEST,
        BaseTitle.FASTEST_SMALLEST,
        BaseTitle.SMALLEST_FASTEST,
    )
    if kind is BuildKind.DOOR:
        return (BaseTitle.FIRST, *shared)
    if kind is BuildKind.EXTENDER:
        return shared
    kind_name = kind.value
    raise InvalidStateError(md_tr(t"records-records-not-supported = Records are not supported for {kind_name}."))


def _resolve_record(
    base_title: BaseTitle,
    kind: BuildKind,
    candidates: Iterable[Candidate],
) -> TitleResolution:
    return resolve_record(base_title, candidates, _timing_methods(kind))
