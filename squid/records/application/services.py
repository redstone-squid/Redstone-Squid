"""Record computation orchestration."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from itertools import groupby

from squid.records.application.models import (
    CandidateFacet,
    CategoryCompetition,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecord,
    HolderHistoryEntry,
    QueueProcessSummary,
    RebuildSummary,
    RecordGap,
    RecordLookupRequest,
    RecordSourceCandidate,
    TitleDiagnosticGap,
)
from squid.records.application.ports import RecordCandidateRepository, RecordRunRepository
from squid.records.domain import (
    BuildKind,
    CategorySemantics,
    DoorCategory,
    ExtenderCategory,
    RecordCandidate,
    RecordClass,
    ResolutionStatus,
    RulesTitleFormatter,
    TitleFormatter,
    VersionScope,
    generate_category_subsets,
    resolve_fastest,
    resolve_smallest,
)
from squid.records.domain.models import DOOR_TIMING_METHODS, EXTENDER_TIMING_METHODS


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
            requested_competition = self._requested_competition(identity, scoped)
            if requested_competition is not None:
                competitions[identity.key] = requested_competition

        scope = VersionScope.ALL_TIME if version_id is None else VersionScope.CURRENT
        records: list[ComputedRecord] = []
        for competition in competitions.values():
            for record_class in (RecordClass.SMALLEST, RecordClass.FASTEST):
                resolution = (
                    resolve_smallest(competition.candidates)
                    if record_class is RecordClass.SMALLEST
                    else resolve_fastest(competition.candidates, _timing_methods(kind))
                )
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
        kinds = tuple(await self._runs.claim_recompute_kinds(limit=limit))
        if not kinds:
            return QueueProcessSummary(kinds=(), rebuild=None)
        if current_version_id is None:
            current_version_id = await self._runs.active_current_version_id()
        try:
            summary = await self.rebuild(current_version_id=current_version_id, kinds=kinds)
        except Exception as error:
            await self._runs.fail_recompute(kinds, str(error))
            raise
        await self._runs.complete_recompute(kinds)
        return QueueProcessSummary(kinds=kinds, rebuild=summary)

    def _eager_competitions(
        self,
        kind: BuildKind,
        candidates: Sequence[RecordSourceCandidate],
    ) -> dict[str, CategoryCompetition]:
        grouped: dict[str, list[tuple[RecordSourceCandidate, tuple[CandidateFacet, ...]]]] = defaultdict(list)
        semantics = CategorySemantics(implications={}, incompatibilities={})
        for source in candidates:
            restrictions_by_id = {facet.id: facet for facet in source.restrictions}
            for restriction_keys in generate_category_subsets(
                (str(facet_id) for facet_id in restrictions_by_id),
                semantics,
                max_size=self._category_limit,
            ):
                restriction_ids = tuple(sorted(int(key) for key in restriction_keys))
                selected = tuple(restrictions_by_id[facet_id] for facet_id in sorted(restriction_ids))
                identity = CategoryIdentity(kind, _base_key(source), restriction_ids)
                grouped[identity.key].append((source, selected))
        return {key: self._make_competition(items, source="eager") for key, items in grouped.items()}

    def _requested_competition(
        self,
        identity: CategoryIdentity,
        candidates: Sequence[RecordSourceCandidate],
    ) -> CategoryCompetition | None:
        items: list[tuple[RecordSourceCandidate, tuple[CandidateFacet, ...]]] = []
        requested_ids = frozenset(identity.restriction_ids)
        for source in candidates:
            restrictions_by_id = {facet.id: facet for facet in source.restrictions}
            if _base_key(source) == identity.base_key and requested_ids <= restrictions_by_id.keys():
                items.append((source, tuple(restrictions_by_id[facet_id] for facet_id in identity.restriction_ids)))
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
        identity = CategoryIdentity(first.kind, _base_key(first), tuple(facet.id for facet in restrictions))
        facets = tuple(sorted((*first.types, *restrictions), key=lambda facet: (facet.kind, facet.id)))
        return CategoryCompetition(
            identity=identity,
            facets=facets,
            category_text=category_text,
            candidates=tuple(item[0].candidate for item in items),
            candidate_version_ids=tuple((item[0].candidate.build_id, item[0].version_ids) for item in items),
            source="public_lookup" if source == "public_lookup" else "eager",
        )


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

    async def lookup_or_materialize(self, request: RecordLookupRequest) -> RebuildSummary:
        """Persist a valid exact category and refresh its build kind."""
        source = tuple(await self._candidates.list_confirmed(request.kind))
        identity = CategoryIdentity(
            request.kind,
            request.base_key,
            tuple(sorted(request.restriction_ids)),
        )
        if not _category_has_candidate(identity, source, request.version_id):
            msg = "No confirmed build satisfies the requested record category."
            raise ValueError(msg)

        ruleset_id = await self._runs.active_ruleset_id()
        await self._runs.save_requested_category(ruleset_id, identity)
        return await self._computation.rebuild(
            current_version_id=request.version_id,
            kinds=(request.kind,),
        )


def _category_has_candidate(
    identity: CategoryIdentity,
    candidates: Iterable[RecordSourceCandidate],
    version_id: int | None,
) -> bool:
    requested = frozenset(identity.restriction_ids)
    return any(
        _base_key(candidate) == identity.base_key
        and requested <= {facet.id for facet in candidate.restrictions}
        and (version_id is None or version_id in candidate.version_ids)
        for candidate in candidates
    )


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
    msg = f"Candidate {source.candidate.build_id} has no typed category facts."
    raise ValueError(msg)


def _category_with_restrictions(
    source: RecordSourceCandidate,
    restrictions: Sequence[CandidateFacet],
) -> DoorCategory | ExtenderCategory:
    wiring = tuple(facet.name for facet in restrictions if facet.restriction_type == "wiring-placement")
    components = tuple(facet.name for facet in restrictions if facet.restriction_type == "component")
    miscellaneous = tuple(facet.name for facet in restrictions if facet.restriction_type == "miscellaneous")
    if source.door is not None:
        return replace(
            source.door,
            wiring_restrictions=wiring,
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
    msg = f"Candidate {source.candidate.build_id} has no typed category facts."
    raise ValueError(msg)


def _timing_methods(kind: BuildKind) -> tuple[str, ...]:
    if kind is BuildKind.DOOR:
        return tuple(DOOR_TIMING_METHODS)
    if kind is BuildKind.EXTENDER:
        return tuple(EXTENDER_TIMING_METHODS)
    msg = f"Fastest records are not supported for {kind.value}."
    raise ValueError(msg)


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
        resolution = (
            resolve_smallest(active)
            if record_class is RecordClass.SMALLEST
            else resolve_fastest(active, _timing_methods(kind))
        )
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
