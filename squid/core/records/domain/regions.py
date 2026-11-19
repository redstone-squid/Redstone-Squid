"""Bounded smallest-record regions in a shared integer opening coordinate.

Callers provide verified domains and formulas in the same coordinate. This is an
arithmetic compiler, not a source-document interpreter or publication service.
It partitions at domain endpoints and exact volume crossings, then checks a
witness with the existing concrete resolver. Every returned region is nonempty.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
from itertools import combinations, pairwise
from math import lcm

from squid.core.builds.domain.expansion_math import ExpressionError, Formula, Polynomial
from squid.core.records.domain.models import Candidate, CandidateGap, ResolutionStatus, TitleResolution
from squid.core.records.domain.region_math import (
    ArithmeticBudget,
    RegionArithmeticError,
    RegionLimitError,
    integer_sign_intervals,
    on_progression,
    polynomial_phases,
)
from squid.core.records.domain.resolution import resolve_smallest


@dataclass(frozen=True, slots=True)
class IntegerProgression:
    """Integers inside inclusive bounds satisfying n % period == residue."""

    minimum: int = 1
    maximum: int | None = None
    period: int = 1
    residue: int = 0

    def __post_init__(self) -> None:
        if self.minimum < 1 or (self.maximum is not None and self.maximum < self.minimum):
            msg = "Opening domains need positive, ordered integer bounds."
            raise RegionArithmeticError(msg)
        if self.period < 1 or not 0 <= self.residue < self.period:
            msg = "A progression needs a positive period and a residue in that period."
            raise RegionArithmeticError(msg)

    def contains(self, value: int) -> bool:
        return (
            value >= self.minimum
            and (self.maximum is None or value <= self.maximum)
            and value % self.period == self.residue
        )


@dataclass(frozen=True, slots=True)
class SmallestRegionCandidate:
    """One configuration with a union of verified progressions.

    A fixed-size door has a singleton domain. Alternatives have distinct
    configuration IDs even when their build IDs match. None volume is unknown;
    an unknown domain must set membership_known=False, not use an empty domain.
    """

    configuration_id: int
    build_id: int
    domain: tuple[IntegerProgression, ...]
    volume: Formula | None
    completion_at: datetime | None = None
    membership_known: bool = True


@dataclass(frozen=True, slots=True)
class SmallestRecordRegion:
    """A nonempty progression where build holders and configuration winners agree.

    The minimum is a concrete witness. Resolution IDs refer to builds; the
    configuration lists retain alternatives, including ambiguous same-build ties.
    """

    domain: IntegerProgression
    resolution: TitleResolution
    holder_configuration_ids: tuple[int, ...] = ()
    provisional_configuration_ids: tuple[int, ...] = ()


class RegionAnalysisStatus(StrEnum):
    EXACT = "exact"
    UNSUPPORTED = "unsupported"
    LIMIT_EXCEEDED = "limit_exceeded"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class SmallestRegionAnalysis:
    """An exact partition, or a failure with no partially awarded regions."""

    status: RegionAnalysisStatus
    regions: tuple[SmallestRecordRegion, ...] = ()
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class RegionLimits:
    candidates: int = 32
    domain_pieces: int = 128
    period: int = 128
    regions: int = 4096
    arithmetic_steps: int = 50_000


DEFAULT_REGION_LIMITS = RegionLimits()


def smallest_record_regions(
    candidates: Sequence[SmallestRegionCandidate],
    *,
    minimum: int = 1,
    maximum: int | None = None,
    limits: RegionLimits = DEFAULT_REGION_LIMITS,
) -> SmallestRegionAnalysis:
    """Find smallest records over an integer interval, including holes and ties.

    Work depends on polynomial crossings and residue classes, not the number of
    queried sizes. All formulas must use the same single variable. Unsupported
    membership or exhausted limits returns a separate analysis status, never a
    misleading no-candidate result. Completion evidence must already be scoped
    to each configuration's domain by the caller.
    """
    try:
        return _analyze(tuple(candidates), IntegerProgression(minimum, maximum), limits)
    except (RegionLimitError, ExpressionError) as error:
        return SmallestRegionAnalysis(RegionAnalysisStatus.LIMIT_EXCEEDED, diagnostic=str(error))
    except RegionArithmeticError as error:
        return SmallestRegionAnalysis(RegionAnalysisStatus.UNSUPPORTED, diagnostic=str(error))


def _analyze(
    candidates: tuple[SmallestRegionCandidate, ...], query: IntegerProgression, limits: RegionLimits
) -> SmallestRegionAnalysis:
    if len(candidates) > limits.candidates:
        msg = "Too many candidate configurations for region analysis."
        raise RegionLimitError(msg)
    if len({candidate.configuration_id for candidate in candidates}) != len(candidates):
        msg = "Configuration IDs must be unique."
        raise RegionArithmeticError(msg)
    if any(not candidate.membership_known for candidate in candidates):
        msg = "An unproven candidate domain prevents exact region analysis."
        raise RegionArithmeticError(msg)
    variables = {candidate.volume.variables for candidate in candidates if candidate.volume is not None}
    if len(variables) > 1:
        msg = "Candidate formulas must use the same opening coordinate."
        raise RegionArithmeticError(msg)
    bounds = [query.minimum, query.maximum]
    period = 1
    for candidate in candidates:
        if len(candidate.domain) > limits.domain_pieces:
            msg = "A candidate domain exceeds the progression limit."
            raise RegionLimitError(msg)
        for domain in candidate.domain:
            bounds.extend((domain.minimum, domain.maximum))
            if domain.period > limits.period:
                msg = "A candidate domain exceeds the residue-class limit."
                raise RegionLimitError(msg)
            period = lcm(period, domain.period)
        if candidate.volume is not None:
            period = lcm(period, polynomial_phases(candidate.volume, max_period=limits.period).period)
        if period > limits.period:
            msg = "Combined domains and formulas require too many residue classes."
            raise RegionLimitError(msg)
    if any(value is not None and value.bit_length() > 256 for value in bounds):
        msg = "Region input bounds exceed the supported bit limit."
        raise RegionLimitError(msg)
    budget = ArithmeticBudget(limits.arithmetic_steps)
    regions: list[SmallestRecordRegion] = []
    for residue in range(period):
        low = -(-(query.minimum - residue) // period)
        high = None if query.maximum is None else (query.maximum - residue) // period
        if high is not None and low > high:
            continue
        formulas = tuple(
            on_progression(candidate.volume, period, residue) if candidate.volume is not None else None
            for candidate in candidates
        )
        boundaries = {low}
        if high is not None:
            boundaries.add(high + 1)
        for candidate in candidates:
            for domain in candidate.domain:
                if residue % domain.period != domain.residue:
                    continue
                boundaries.add(max(low, -(-(domain.minimum - residue) // period)))
                if domain.maximum is not None:
                    boundaries.add(max(low, (domain.maximum - residue) // period + 1))
        comparable = [formula for formula in formulas if formula is not None]
        comparisons = comparable + [left.add(right.scale(Fraction(-1))) for left, right in combinations(comparable, 2)]
        for polynomial in comparisons:
            for interval in integer_sign_intervals(polynomial, minimum=low, maximum=high, budget=budget):
                boundaries.add(interval.minimum)
                if interval.maximum is not None:
                    boundaries.add(interval.maximum + 1)
        ordered = sorted(boundary for boundary in boundaries if high is None or boundary <= high + 1)
        spans = [*pairwise(ordered), (ordered[-1], None)] if high is None else list(pairwise(ordered))
        for start, stop in spans:
            budget.spend()
            witness = period * start + residue
            domain = IntegerProgression(
                witness, None if stop is None else period * (stop - 1) + residue, period, residue
            )
            region = _resolve_region(candidates, formulas, start, domain, budget)
            if (
                regions
                and regions[-1].domain.period == period
                and regions[-1].domain.residue == residue
                and regions[-1].domain.maximum == witness - period
                and replace(regions[-1], domain=domain) == region
            ):
                previous = regions.pop()
                region = replace(region, domain=replace(domain, minimum=previous.domain.minimum))
            regions.append(region)
            if len(regions) > limits.regions:
                msg = "The exact partition exceeds the region limit."
                raise RegionLimitError(msg)
    return SmallestRegionAnalysis(RegionAnalysisStatus.EXACT, tuple(regions))


def _resolve_region(
    sources: tuple[SmallestRegionCandidate, ...],
    formulas: tuple[Polynomial | None, ...],
    k: int,
    domain: IntegerProgression,
    budget: ArithmeticBudget,
) -> SmallestRecordRegion:
    witness = domain.minimum
    candidates: list[Candidate] = []
    for source, formula in zip(sources, formulas, strict=True):
        if formula is None or not any(domain.contains(witness) for domain in source.domain):
            continue
        budget.spend()
        value = formula.evaluate((k,))
        assert source.volume is not None
        budget.spend()
        if source.volume.evaluate((witness,)) != value:
            msg = "A lowered formula disagrees with its original at the witness."
            raise RegionArithmeticError(msg)
        if value <= 0 or value.denominator != 1:
            continue
        candidates.append(Candidate(source.configuration_id, source.completion_at, value.numerator))
    result = resolve_smallest(candidates)
    sources_by_id = {source.configuration_id: source for source in sources}

    def build_ids(ids: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(sorted({sources_by_id[id].build_id for id in ids}))

    resolution = TitleResolution(
        result.status,
        build_ids(result.holder_ids),
        build_ids(result.provisional_holder_ids),
        tuple(dict.fromkeys(CandidateGap(sources_by_id[gap.build_id].build_id, gap.field) for gap in result.gaps)),
    )
    if resolution.status is ResolutionStatus.UNRESOLVED and len(resolution.provisional_holder_ids) == 1:
        resolution = TitleResolution(ResolutionStatus.RESOLVED, holder_ids=resolution.provisional_holder_ids)
    return SmallestRecordRegion(domain, resolution, result.holder_ids, result.provisional_holder_ids)
