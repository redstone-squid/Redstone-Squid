"""Translate stored family facts into exact smallest-record width regions.

Fixing opening height and depth turns each affine inverse parameter mapping into
one polynomial in width, even for XYZ families. We partition wherever opening
consistency, bounds, progressions, or domain predicates can change, then consult
the point evaluator for membership. Unknown membership blocks the whole query;
known exclusions do not. Source record claims and sample metrics never compete.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from itertools import pairwise
from math import lcm
from typing import Literal, Self

from pydantic import field_validator, model_validator

from squid.builds.domain.expansion_evaluation import (
    ExpansionDiagnostic,
    NormalizedBranch,
    NormalizedExpansion,
    PointStatus,
    evaluate_expansion,
    normalize_expansion,
)
from squid.builds.domain.expansion_math import (
    Comparison,
    DomainValue,
    ExpressionError,
    Formula,
    Polynomial,
    Predicate,
    scale_formula,
    substitute_formula,
)
from squid.records.application.live import DoorRecordCategory, LookupDimension
from squid.records.application.models import CandidateBuild
from squid.records.domain.models import BaseTitle
from squid.records.domain.region_math import (
    ArithmeticBudget,
    RegionArithmeticError,
    RegionLimitError,
    integer_sign_intervals,
    on_progression,
    polynomial_phases,
)
from squid.records.domain.regions import (
    DEFAULT_REGION_LIMITS,
    IntegerProgression,
    RegionAnalysisStatus,
    RegionLimits,
    SmallestRegionAnalysis,
    SmallestRegionCandidate,
    smallest_record_regions,
)

_VARIABLES = ("width",)
_WIDTH = Polynomial.create(_VARIABLES, {(1,): Fraction(1)})


class DoorRangeQuery(DoorRecordCategory):
    """A smallest-record query varying width at fixed opening height and depth."""

    axis: Literal["width"] = "width"
    base_title: Literal[BaseTitle.SMALLEST] = BaseTitle.SMALLEST
    height: LookupDimension
    depth: LookupDimension = 1
    minimum: LookupDimension = 1
    maximum: LookupDimension | None = None

    @field_validator("height", "depth", "minimum", "maximum", mode="before")
    @classmethod
    def validate_dimensions(cls, value: object) -> object:
        if value is not None and type(value) is not int:
            msg = "Opening dimensions and bounds must be integers."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_width_bounds(self) -> Self:
        if self.maximum is not None and self.maximum < self.minimum:
            msg = "The maximum width cannot be smaller than the minimum width."
            raise ValueError(msg)
        return self


@dataclass(frozen=True, slots=True)
class DoorRegionConfiguration:
    """Map local compiler IDs back to stored builds and expansion branches."""

    id: int
    build_id: int
    branch_index: int | None
    diagnostics: tuple[ExpansionDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class DoorRangeAnalysis:
    analysis: SmallestRegionAnalysis
    configurations: tuple[DoorRegionConfiguration, ...]


@dataclass(frozen=True, slots=True)
class DoorRangeResult:
    """A live analysis of confirmed facts, without a persistent record identity."""

    query: DoorRangeQuery
    evaluated_at: datetime
    analysis: SmallestRegionAnalysis
    configurations: tuple[DoorRegionConfiguration, ...]


def analyze_door_widths(
    sources: Sequence[CandidateBuild],
    *,
    height: int,
    depth: int = 1,
    minimum: int = 1,
    maximum: int | None = None,
    limits: RegionLimits = DEFAULT_REGION_LIMITS,
) -> DoorRangeAnalysis:
    """Analyze candidates already filtered by orientation, taxonomy and version.

    Families may vary along multiple axes; this query varies opening width only.
    Fixed candidates require typed door_opening dimensions. Failure returns no
    partial awards and includes the configuration responsible when identifiable.
    """
    configurations: list[DoorRegionConfiguration] = []
    budget = ArithmeticBudget(limits.arithmetic_steps)
    try:
        query = IntegerProgression(minimum, maximum)
        candidates = _translate_sources(sources, height, depth, query, limits, budget, configurations)
        analysis = smallest_record_regions(
            candidates,
            minimum=minimum,
            maximum=maximum,
            limits=replace(limits, arithmetic_steps=budget.remaining),
        )
    except (RegionLimitError, ExpressionError) as error:
        analysis = SmallestRegionAnalysis(RegionAnalysisStatus.LIMIT_EXCEEDED, diagnostic=str(error))
    except RegionArithmeticError as error:
        analysis = SmallestRegionAnalysis(RegionAnalysisStatus.UNSUPPORTED, diagnostic=str(error))
    return DoorRangeAnalysis(analysis, tuple(configurations))


def _translate_sources(
    sources: Sequence[CandidateBuild],
    height: int,
    depth: int,
    query: IntegerProgression,
    limits: RegionLimits,
    budget: ArithmeticBudget,
    configurations: list[DoorRegionConfiguration],
) -> list[SmallestRegionCandidate]:
    candidates: list[SmallestRegionCandidate] = []
    if len(sources) > limits.candidates:
        msg = "Too many source builds for width-region analysis."
        raise RegionLimitError(msg)
    if height < 1 or depth < 1:
        msg = "Opening height and depth must be positive."
        raise RegionArithmeticError(msg)
    for source in sorted(sources, key=lambda item: item.candidate.build_id):
        build = source.candidate
        if source.expansion is None:
            id = len(configurations)
            configurations.append(DoorRegionConfiguration(id, build.build_id, None))
            if source.door_opening is None:
                msg = f"Fixed build {build.build_id} has no verified opening dimensions."
                raise RegionArithmeticError(msg)
            width, fixed_height, fixed_depth = source.door_opening
            if fixed_height != height or fixed_depth != depth or not query.contains(width):
                continue
            candidates.append(
                SmallestRegionCandidate(
                    id,
                    build.build_id,
                    (IntegerProgression(width, width),),
                    Polynomial.constant(_VARIABLES, Fraction(build.fixed_volume))
                    if build.fixed_volume is not None
                    else None,
                    build.completion_at,
                )
            )
            continue
        expansion = normalize_expansion(source.expansion)
        if not expansion.branches:
            configurations.append(
                DoorRegionConfiguration(
                    len(configurations),
                    build.build_id,
                    None,
                    expansion.diagnostics,
                )
            )
            msg = f"Build {build.build_id} has no normalizable expansion branches."
            raise RegionArithmeticError(msg)
        for branch in expansion.branches:
            id = len(configurations)
            configurations.append(DoorRegionConfiguration(id, build.build_id, branch.index, branch.diagnostics))
            if len(configurations) > limits.candidates:
                msg = "Too many source configurations for width-region analysis."
                raise RegionLimitError(msg)
            candidate = _branch_candidate(expansion, branch, id, build.build_id, height, depth, query, limits, budget)
            candidates.append(candidate)
    return candidates


def _branch_candidate(
    expansion: NormalizedExpansion,
    branch: NormalizedBranch,
    id: int,
    build_id: int,
    height: int,
    depth: int,
    query: IntegerProgression,
    limits: RegionLimits,
    budget: ArithmeticBudget,
) -> SmallestRegionCandidate:
    if branch.inverse is None:
        msg = f"Build {build_id}, branch {branch.index}: opening coordinates do not determine parameters."
        raise RegionArithmeticError(msg)
    parameters = tuple(
        Polynomial.create(_VARIABLES, {(1,): row[0], (0,): row[1] * height + row[2] * depth + row[3]})
        for row in branch.inverse
    )

    def substitute(formula: Formula) -> Formula:
        return substitute_formula(formula, parameters, variables=_VARIABLES)

    # A zero comparison captures every place an opening equation can change truth.
    comparisons = [
        (DomainValue(substitute(formula)), DomainValue(expected))
        for formula, expected in zip(branch.opening, (_WIDTH, _constant(height), _constant(depth)), strict=True)
    ]
    periodic: list[Formula] = list(parameters)
    for parameter, coordinate in zip(expansion.source.parameters, parameters, strict=True):
        thresholds = [value for value in (parameter.minimum, parameter.maximum) if value is not None]
        start = parameter.minimum
        if start is not None and parameter.initial_steps is not None:
            for step in parameter.initial_steps:
                start += step
                thresholds.append(start)
            if parameter.repeating_steps is not None:
                # On each residue, the parameter changes by multiples of the
                # repeating cycle, so membership cannot change between bounds.
                periodic.append(scale_formula(coordinate, Fraction(1, sum(parameter.repeating_steps))))
        comparisons.extend((DomainValue(coordinate), DomainValue(_constant(value))) for value in thresholds)
    if branch.domain is not None:
        comparisons.extend(
            (
                DomainValue(substitute(comparison.left.expression), comparison.left.modulus),
                DomainValue(substitute(comparison.right.expression), comparison.right.modulus),
            )
            for comparison in _comparisons(branch.domain)
        )
    for left, right in comparisons:
        # Plain rational comparisons need sign boundaries, not integral values.
        # Residue refinement is needed only for rounding and modulo here.
        periodic.extend(
            scale_formula(value.expression, Fraction(1, value.modulus or 1))
            for value in (left, right)
            if value.modulus is not None or not isinstance(value.expression, Polynomial)
        )
    period = 1
    for formula in periodic:
        budget.spend()
        period = lcm(period, polynomial_phases(formula, max_period=limits.period).period)
        if period > limits.period:
            msg = f"Build {build_id}, branch {branch.index}: domain needs too many residue classes."
            raise RegionLimitError(msg)
    domains: list[IntegerProgression] = []
    volume = substitute(branch.volume) if branch.volume is not None else None
    # Evaluate only this branch; other alternatives are translated independently.
    single = replace(expansion, branches=(branch,))
    for residue in range(period):
        low = -(-(query.minimum - residue) // period)
        high = None if query.maximum is None else (query.maximum - residue) // period
        if high is not None and high < low:
            continue
        boundaries = {low}
        if high is not None:
            boundaries.add(high + 1)
        for left, right in comparisons:
            budget.spend()
            a = _domain_polynomial(left, period, residue)
            b = _domain_polynomial(right, period, residue)
            # A nonintegral modulo operand fails throughout this residue class.
            # The point evaluator decides whether boolean short-circuiting reaches it.
            if a is None or b is None:
                continue
            difference = a.add(b.scale(Fraction(-1)))
            for interval in integer_sign_intervals(difference, minimum=low, maximum=high, budget=budget):
                boundaries.add(interval.minimum)
                if interval.maximum is not None:
                    boundaries.add(interval.maximum + 1)
        ordered = sorted(boundaries)
        spans = [*pairwise(ordered), (ordered[-1], None)] if high is None else list(pairwise(ordered))
        for start, stop in spans:
            budget.spend()
            width = period * start + residue
            point = evaluate_expansion(single, (width, height, depth)).configurations[0]
            if point.status is PointStatus.UNRESOLVED:
                msg = f"Build {build_id}, branch {branch.index}: membership is unknown at width {width}."
                raise RegionArithmeticError(msg)
            if point.status is PointStatus.SUPPORTED:
                if volume is not None:
                    value = volume.evaluate((width,))
                    measured = value.numerator if value > 0 and value.denominator == 1 else None
                    if measured != point.volume:
                        msg = f"Build {build_id}, branch {branch.index}: translated volume disagrees with point evaluation."
                        raise RegionArithmeticError(msg)
                domain = IntegerProgression(
                    width, None if stop is None else period * (stop - 1) + residue, period, residue
                )
                if domains and domains[-1].residue == residue and domains[-1].maximum == width - period:
                    previous = domains.pop()
                    domain = replace(domain, minimum=previous.minimum)
                domains.append(domain)
                if len(domains) > limits.domain_pieces:
                    msg = f"Build {build_id}, branch {branch.index}: too many domain pieces."
                    raise RegionLimitError(msg)
    return SmallestRegionCandidate(id, build_id, tuple(domains), volume)


def _constant(value: int) -> Polynomial:
    return Polynomial.constant(_VARIABLES, Fraction(value))


def _comparisons(predicate: Predicate) -> tuple[Comparison, ...]:
    if isinstance(predicate, Comparison):
        return (predicate,)
    return tuple(comparison for child in predicate.children for comparison in _comparisons(child))


def _domain_polynomial(value: DomainValue, period: int, residue: int) -> Polynomial | None:
    polynomial = on_progression(value.expression, period, residue)
    if value.modulus is None:
        return polynomial
    constant = polynomial.evaluate((0,))
    if constant.denominator != 1:
        return None
    return _constant(constant.numerator % value.modulus)
