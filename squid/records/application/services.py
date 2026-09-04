"""Record computation orchestration."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from itertools import combinations, groupby, product

from squid.core.errors import DataIntegrityError, InvalidStateError, ValidationError
from squid.core.i18n import tr
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page
from squid.records.application.models import (
    CandidateFacet,
    CategoryCompetition,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecord,
    HolderHistoryEntry,
    PublicRecordDetail,
    PublishedRecord,
    QueueProcessSummary,
    RebuildSummary,
    RecordGap,
    RecordLookupRequest,
    RecordSourceCandidate,
    TitleDiagnosticGap,
)
from squid.records.application.ports import PublicBuildSummaryReader, RecordCandidateRepository, RecordRunRepository
from squid.records.domain import (
    BuildKind,
    CategoryText,
    DoorCategory,
    ExtenderCategory,
    RecordCandidate,
    RecordClass,
    RecordResolution,
    ResolutionStatus,
    RulesTitleFormatter,
    TitleFormatter,
    VersionScope,
    resolve_fastest,
    resolve_fastest_smallest,
    resolve_first,
    resolve_smallest,
    resolve_smallest_fastest,
)
from squid.records.domain.models import DOOR_TIMING_METHODS, EXTENDER_TIMING_METHODS
from squid.records.errors import NoMatchingRecordCategoryError, RecordDefinitionNotFoundError


class RecordComputationService:
    """Build complete versioned record runs from confirmed build facts."""

    def __init__(
        self,
        candidates: RecordCandidateRepository,
        runs: RecordRunRepository,
        *,
        formatter: TitleFormatter | None = None,
        category_limit: int = 8,
    ) -> None:
        self._candidates = candidates
        self._runs = runs
        self._formatter = formatter or RulesTitleFormatter()
        self._category_limit = category_limit

    async def rebuild(
        self,
        *,
        current_version_id: int | None = None,
        kinds: Sequence[BuildKind] = (BuildKind.DOOR, BuildKind.EXTENDER),
    ) -> RebuildSummary:
        """Recompute and atomically activate every requested kind/scope."""
        ruleset_id = await self._runs.active_ruleset_id()
        run_ids: list[int] = []
        records: list[ComputedRecord] = []
        for kind in kinds:
            source = tuple(await self._candidates.list_confirmed(kind))
            requested = tuple(await self._runs.list_requested_categories(kind))
            all_time = self._compute_scope(
                ruleset_id,
                kind,
                source,
                version_id=None,
                pinned_current_version_id=current_version_id,
                requested=requested,
            )
            run_ids.append(await self._runs.activate(all_time))
            records.extend(all_time.records)

            if current_version_id is not None:
                current = self._compute_scope(
                    ruleset_id,
                    kind,
                    source,
                    version_id=current_version_id,
                    pinned_current_version_id=current_version_id,
                    requested=requested,
                )
                run_ids.append(await self._runs.activate(current))
                records.extend(current.records)

        return RebuildSummary(
            run_ids=tuple(run_ids),
            definitions=len(records),
            resolved=sum(record.resolution.status is ResolutionStatus.RESOLVED for record in records),
            unresolved=sum(record.resolution.status is ResolutionStatus.UNRESOLVED for record in records),
        )

    def _compute_scope(
        self,
        ruleset_id: int,
        kind: BuildKind,
        source: Sequence[RecordSourceCandidate],
        *,
        version_id: int | None,
        pinned_current_version_id: int | None,
        requested: Sequence[CategoryIdentity],
    ) -> ComputationBatch:
        scoped = tuple(candidate for candidate in source if version_id is None or version_id in candidate.version_ids)
        competitions = self._eager_competitions(kind, scoped)
        for identity in requested:
            competition = self.requested_competition(identity, scoped)
            if competition is not None:
                competitions[identity.key] = competition

        scope = VersionScope.ALL_TIME if version_id is None else VersionScope.CURRENT
        records: list[ComputedRecord] = []
        for competition in competitions.values():
            for record_class in _record_classes(kind):
                resolution = _resolve_record(record_class, kind, competition.candidates)
                records.append(
                    ComputedRecord(
                        record_class=record_class,
                        scope=scope,
                        version_id=version_id,
                        competition=competition,
                        title=self._formatter.format_record(record_class, competition.category_text),
                        resolution=resolution,
                        broken_holder_ids=_broken_holders(
                            resolution.holder_ids,
                            competition.candidate_version_ids,
                            pinned_current_version_id,
                        ),
                        history=_reconstruct_history(record_class, kind, competition.candidates),
                        history_complete=all(
                            candidate.completion_at is not None for candidate in competition.candidates
                        ),
                    )
                )
        return ComputationBatch(
            ruleset_id=ruleset_id,
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
        """Claim queued scopes, rebuild them, and acknowledge only on success."""
        lease = await self._runs.claim_recompute_kinds(limit=limit)
        if not lease:
            return QueueProcessSummary(kinds=(), rebuild=None)
        if current_version_id is None:
            current_version_id = await self._runs.active_current_version_id()
        try:
            summary = await self.rebuild(current_version_id=current_version_id, kinds=lease.kinds)
        except Exception as error:
            await self._runs.fail_recompute(lease, str(error))
            raise
        await self._runs.complete_recompute(lease)
        return QueueProcessSummary(kinds=lease.kinds, rebuild=summary)

    def _eager_competitions(
        self,
        kind: BuildKind,
        candidates: Sequence[RecordSourceCandidate],
    ) -> dict[str, CategoryCompetition]:
        grouped: dict[str, list[tuple[RecordSourceCandidate, tuple[CandidateFacet, ...]]]] = defaultdict(list)
        thresholds = _observed_thresholds(candidates)
        for source in candidates:
            choices = _restriction_choice_groups(source, thresholds)
            for selected in _restriction_combinations(choices, max_size=self._category_limit):
                identity = _category_identity(kind, _base_key(source), selected)
                grouped[identity.key].append((source, selected))
        return {key: self._make_competition(items, source="eager") for key, items in grouped.items()}

    def requested_competition(
        self,
        identity: CategoryIdentity,
        candidates: Sequence[RecordSourceCandidate],
    ) -> CategoryCompetition | None:
        """Build the competition for one requested exact category, or None if no candidate qualifies."""
        items: list[tuple[RecordSourceCandidate, tuple[CandidateFacet, ...]]] = []
        requested_ids = frozenset(identity.restriction_ids)
        requested_values = {tag_id: (operator, value) for tag_id, operator, value in identity.restriction_values}
        for source in candidates:
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
                    if facet.record_operator != operator or not _satisfies(facet, threshold):
                        break
                    selected.append(replace(facet, category_value=threshold))
                else:
                    items.append((source, tuple(selected)))
        return self._make_competition(items, source="public_lookup") if items else None

    def _make_competition(
        self,
        items: Sequence[tuple[RecordSourceCandidate, tuple[CandidateFacet, ...]]],
        *,
        source: str,
    ) -> CategoryCompetition:
        first, restrictions = items[0]
        category = _category_with_restrictions(first, restrictions)
        category_text = (
            self._formatter.format_door(category)
            if isinstance(category, DoorCategory)
            else self._formatter.format_extender(category)
        )
        identity = _category_identity(first.kind, _base_key(first), restrictions)
        facets = tuple(sorted((*first.types, *restrictions), key=lambda facet: (facet.kind, facet.id)))
        return CategoryCompetition(
            identity=identity,
            facets=facets,
            category_text=category_text,
            candidates=tuple(item[0].candidate for item in items),
            candidate_version_ids=tuple((item[0].candidate.build_id, item[0].version_ids) for item in items),
            source="public_lookup" if source == "public_lookup" else "eager",
        )

    def format_record_titles(self, competition: CategoryCompetition) -> dict[RecordClass, CategoryText]:
        """Render the per-class titles a definition stores for this competition."""
        return {
            record_class: self._formatter.format_record(record_class, competition.category_text)
            for record_class in _record_classes(competition.identity.kind)
        }


class RecordService:
    """Queries and explicit category materialization."""

    def __init__(
        self,
        candidates: RecordCandidateRepository,
        runs: RecordRunRepository,
        computation: RecordComputationService,
    ) -> None:
        self._candidates = candidates
        self._runs = runs
        self._computation = computation

    async def gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        """Return decisive facts missing from active record results."""
        return await self._runs.list_gaps(kind=kind)

    async def title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]:
        """Return active canonical record titles requiring taxonomy review."""
        return await self._runs.list_title_gaps(kind=kind)

    async def get(self, result_id: int) -> PublishedRecord | None:
        """Return one published computed record result."""
        return await self._runs.get_published_record(result_id)

    async def list_page(
        self,
        *,
        selector: PageSelector = FIRST_PAGE,
        descending: bool = True,
        page_size: int = 20,
    ) -> Page[PublishedRecord]:
        """Return one page of published record results in display order."""
        rows = await self._runs.list_published_records(
            offset=selector.offset,
            after_id=selector.after_id,
            before_id=selector.before_id,
            descending=descending,
            # One row past the page proves whether another page follows.
            limit=page_size + 1,
        )
        return keyset_page(
            rows,
            selector=selector,
            page_size=page_size,
            total=await self._runs.count_published_records(),
            keyset=True,
            id_of=lambda record: record.id,
        )

    async def materialize_definition(
        self,
        definition_id: int,
        *,
        kind: BuildKind | None = None,
        version_id: int | None = None,
    ) -> RebuildSummary:
        """Re-materialize the exact category an existing definition identifies."""
        identity = await self._runs.get_definition_identity(definition_id)
        if identity is None:
            raise RecordDefinitionNotFoundError(definition_id)
        if kind is not None and kind is not identity.kind:
            actual = identity.kind.value
            requested = kind.value
            raise ValidationError(tr(t"Record category {definition_id} is a {actual} category, not {requested}."))
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
        source = tuple(await self._candidates.list_confirmed(request.kind))
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
        competition = self._computation.requested_competition(identity, scoped)
        if competition is None:
            raise NoMatchingRecordCategoryError(kind=request.kind.value, base_key=request.base_key)

        ruleset_id = await self._runs.active_ruleset_id()
        await self._runs.save_requested_category(
            ruleset_id, identity, self._computation.format_record_titles(competition)
        )
        return await self._computation.rebuild(
            current_version_id=request.version_id,
            kinds=(request.kind,),
        )


class PublicRecordQueryService:
    """Assemble public-complete record details across record and build read boundaries."""

    def __init__(self, records: RecordRunRepository, builds: PublicBuildSummaryReader) -> None:
        self._records = records
        self._builds = builds

    async def get(self, standing_id: int) -> PublicRecordDetail | None:
        """Return one public detail, rejecting missing or private holder references."""
        standing = await self._records.get_published_record(standing_id)
        if standing is None:
            return None
        found = await self._builds.get_public_summaries(standing.holder_build_ids)
        by_id = {build.id: build for build in found}
        unavailable_ids = [build_id for build_id in standing.holder_build_ids if build_id not in by_id]
        if unavailable_ids:
            msg = "A published record references holder builds that are unavailable to the public catalogue."
            raise DataIntegrityError(
                msg,
                context={"record_id": standing.id, "unavailable_holder_build_ids": unavailable_ids},
            )
        return PublicRecordDetail(
            standing=standing,
            holder_builds=tuple(by_id[build_id] for build_id in standing.holder_build_ids),
        )


def _observed_thresholds(
    candidates: Sequence[RecordSourceCandidate],
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
    source: RecordSourceCandidate,
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
    msg = tr(t"Parameterized record restriction is missing a category value.")
    raise DataIntegrityError(msg)


def _coerce_category_value(facet: CandidateFacet, value: str) -> Decimal | str | bool:
    if facet.value_type == "numeric":
        return Decimal(value)
    if facet.value_type == "boolean":
        if value not in {"true", "false"}:
            raise InvalidStateError(tr(t"Invalid boolean category value {value!r}."))
        return value == "true"
    return value


def _base_key(source: RecordSourceCandidate) -> str:
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
    raise DataIntegrityError(tr(t"Candidate {build_id} has no typed category facts."))


def _category_with_restrictions(
    source: RecordSourceCandidate,
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
    raise DataIntegrityError(tr(t"Candidate {build_id} has no typed category facts."))


def _timing_methods(kind: BuildKind) -> tuple[str, ...]:
    if kind is BuildKind.DOOR:
        return tuple(DOOR_TIMING_METHODS)
    if kind is BuildKind.EXTENDER:
        return tuple(EXTENDER_TIMING_METHODS)
    kind_name = kind.value
    raise InvalidStateError(tr(t"Fastest records are not supported for {kind_name}."))


def _record_classes(kind: BuildKind) -> tuple[RecordClass, ...]:
    shared = (
        RecordClass.FASTEST,
        RecordClass.SMALLEST,
        RecordClass.FASTEST_SMALLEST,
        RecordClass.SMALLEST_FASTEST,
    )
    if kind is BuildKind.DOOR:
        return (RecordClass.FIRST, *shared)
    if kind is BuildKind.EXTENDER:
        return shared
    kind_name = kind.value
    raise InvalidStateError(tr(t"Records are not supported for {kind_name}."))


def _resolve_record(
    record_class: RecordClass,
    kind: BuildKind,
    candidates: Iterable[RecordCandidate],
) -> RecordResolution:
    if record_class is RecordClass.FIRST:
        return resolve_first(candidates)
    if record_class is RecordClass.FASTEST:
        return resolve_fastest(candidates, _timing_methods(kind))
    if record_class is RecordClass.SMALLEST:
        return resolve_smallest(candidates)
    if record_class is RecordClass.FASTEST_SMALLEST:
        return resolve_fastest_smallest(candidates, _timing_methods(kind))
    return resolve_smallest_fastest(candidates, _timing_methods(kind))


def _broken_holders(
    holder_ids: tuple[int, ...],
    candidate_version_ids: tuple[tuple[int, frozenset[int]], ...],
    pinned_current_version_id: int | None,
) -> tuple[int, ...]:
    if pinned_current_version_id is None:
        return ()
    versions_by_build = dict(candidate_version_ids)
    return tuple(
        build_id for build_id in holder_ids if pinned_current_version_id not in versions_by_build.get(build_id, ())
    )


def _reconstruct_history(
    record_class: RecordClass,
    kind: BuildKind,
    candidates: Sequence[RecordCandidate],
) -> tuple[HolderHistoryEntry, ...]:
    dated = sorted(
        (candidate for candidate in candidates if candidate.completion_at is not None),
        key=_known_completion,
    )
    active: list[RecordCandidate] = []
    periods: list[HolderHistoryEntry] = []
    for completion_at, group in groupby(dated, key=_known_completion):
        active.extend(group)
        resolution = _resolve_record(record_class, kind, active)
        if resolution.status is not ResolutionStatus.RESOLVED:
            continue
        if periods and periods[-1].build_ids == resolution.holder_ids:
            continue
        if periods:
            previous = periods[-1]
            periods[-1] = replace(previous, held_until=completion_at)
        periods.append(
            HolderHistoryEntry(
                build_ids=resolution.holder_ids,
                held_from=completion_at,
                held_until=None,
            )
        )
    return tuple(periods)


def _known_completion(candidate: RecordCandidate) -> datetime:
    assert candidate.completion_at is not None
    return candidate.completion_at
