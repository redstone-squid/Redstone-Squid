"""Normalize source claims and evaluate individual opening configurations.

Normalization compiles the supported syntax without changing source documents.
Evaluation proves membership at one integer opening size, then evaluates volume
and timing separately. It neither proves validity over an entire family nor
publishes records. Alternative branches remain separate configurations.
"""

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from squid.builds.domain.expansion import DoorExpansion, ExpansionBranch, ExpansionParameter
from squid.builds.domain.expansion_math import (
    MAX_BITS,
    ExpressionError,
    ExpressionParser,
    Formula,
    Polynomial,
    Predicate,
    scale_formula,
)

_TIMING_METHODS = ("opening", "opening_visible", "closing", "closing_visible", "opening_reset", "closing_reset")
_UNIT_SCALES = {"game_tick": 1, "redstone_tick": 2, "second": 20}


class PointStatus(StrEnum):
    """Whether one configuration exists at the evaluated opening.

    UNRESOLVED means the source makes a claim that does not settle this size:
    the size lies beyond an unknown bound, or the opening expressions use syntax
    the normalizer cannot invert. UNMAPPED means the opening width or height is absent,
    so the source never says which sizes the configuration has. Only SUPPORTED
    proves membership at this opening; callers decide how to handle uncertainty.
    """

    SUPPORTED = "supported"
    OUTSIDE_DOMAIN = "outside_domain"
    UNRESOLVED = "unresolved"
    UNMAPPED = "unmapped"


@dataclass(frozen=True, slots=True)
class ExpansionDiagnostic:
    """A source field that needs more facts or a more capable normalizer."""

    path: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class TimingFormula:
    expression: Formula
    source_path: str


@dataclass(frozen=True, slots=True)
class NormalizedBranch:
    index: int
    source: ExpansionBranch
    opening: tuple[Formula, ...]
    # False when the source omits the opening width or height, as opposed to
    # stating one the normalizer cannot use. An omitted depth means one block.
    opening_stated: bool
    inverse: tuple[tuple[Fraction, ...], ...] | None
    domain: Predicate | None
    domain_valid: bool
    volume: Formula | None
    timings: tuple[tuple[TimingFormula | None, ...], ...]
    diagnostics: tuple[ExpansionDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class NormalizedExpansion:
    """Compiled claims with their original document and branch diagnostics."""

    source: DoorExpansion
    branches: tuple[NormalizedBranch, ...]
    diagnostics: tuple[ExpansionDiagnostic, ...] = ()

    @classmethod
    def from_source(cls, source: DoorExpansion) -> NormalizedExpansion:
        """Compile supported claims; failures stay attached to their source fields.

        Source record-breaking domains and completion claims are not eligibility
        constraints. Only branch.domain and structured parameter domains restrict
        membership. Names are case-sensitive; prose meanings are not interpreted.
        """
        try:
            parser = ExpressionParser(tuple(parameter.name for parameter in source.parameters))
        except ExpressionError as error:
            return cls(source, (), (ExpansionDiagnostic("parameters", "unsupported", str(error)),))
        if not source.branches:
            return cls(source, (), (ExpansionDiagnostic("branches", "missing", "No configurations supplied."),))
        return cls(source, tuple(_normalize_branch(parser, branch, i) for i, branch in enumerate(source.branches)))

    def evaluate(self, opening: tuple[int, int, int]) -> PointEvaluation:
        """Evaluate each branch at a positive integer opening, without enumerating sizes.

        Unknown domains produce unresolved membership. Outside branches do not
        compete, and neither do unmapped ones, which lack an opening width or height.
        Supported branches may still have unknown volume or timings; callers must inspect
        metrics and diagnostics before ranking configurations.
        """
        if len(opening) != 3 or any(
            type(value) is not int or value <= 0 or value.bit_length() > MAX_BITS for value in opening
        ):
            msg = "Opening size requires three positive integers of at most 256 bits."
            raise ExpressionError(msg)
        return PointEvaluation(
            opening,
            tuple(_evaluate_branch(self, branch, opening) for branch in self.branches),
            self.diagnostics,
        )


@dataclass(frozen=True, slots=True)
class PointConfiguration:
    """One branch's result; supported membership does not imply known metrics.

    Volume is cumulative wiring volume. Timings use six game-tick slots in Door
    Rules priority order, with None for unknown or invalid measurements. Branch
    completion claims remain source text and are not interpreted as evidence of
    completion at every size.
    """

    branch_index: int
    status: PointStatus
    parameters: tuple[tuple[str, int], ...] = ()
    volume: int | None = None
    timing_variants: tuple[tuple[int | None, ...], ...] = ()
    diagnostics: tuple[ExpansionDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class PointEvaluation:
    opening: tuple[int, int, int]
    configurations: tuple[PointConfiguration, ...]
    diagnostics: tuple[ExpansionDiagnostic, ...] = ()


def _inverse(opening: tuple[Formula, ...], count: int) -> tuple[tuple[Fraction, ...], ...]:
    """Find a rational left inverse of the affine map using Gaussian elimination.

    Only affine equations recover parameters. Remaining opening equations,
    including rounded expressions, are checked at evaluation.
    Requiring full column rank avoids hidden configuration searches.
    """
    if len(opening) != 3:
        msg = "All three opening dimensions must be known expressions."
        raise ExpressionError(msg)
    matrix: list[list[Fraction]] = []
    for axis, formula in enumerate(opening):
        if not isinstance(formula, Polynomial) or formula.degree > 1:
            continue
        coefficients = [Fraction(0)] * count
        constant = Fraction(0)
        for powers, value in formula.terms:
            if sum(powers) == 0:
                constant = value
            else:
                coefficients[powers.index(1)] = value
        matrix.append(coefficients + [Fraction(int(axis == i)) for i in range(3)] + [-constant])
    for column in range(count):
        pivot = next((row for row in range(column, len(matrix)) if matrix[row][column]), None)
        if pivot is None:
            msg = "The affine opening dimensions do not uniquely determine every declared parameter."
            raise ExpressionError(msg)
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        divisor = matrix[column][column]
        matrix[column] = [value / divisor for value in matrix[column]]
        for row in range(len(matrix)):
            if row != column:
                scalar = matrix[row][column]
                matrix[row] = [a - scalar * b for a, b in zip(matrix[row], matrix[column], strict=True)]
    return tuple(tuple(row[count:]) for row in matrix[:count])


def _normalize_branch(parser: ExpressionParser, source: ExpansionBranch, index: int) -> NormalizedBranch:
    diagnostics: list[ExpansionDiagnostic] = []
    prefix = f"branches[{index}]"

    def formula(value: str | None, field: str) -> Formula | None:
        if value is None:
            diagnostics.append(ExpansionDiagnostic(f"{prefix}.{field}", "missing", "No expression supplied."))
            return None
        try:
            return parser.formula(value)
        except ExpressionError as error:
            diagnostics.append(ExpansionDiagnostic(f"{prefix}.{field}", "unsupported", str(error)))
            return None

    opening = tuple(
        parsed
        for field in ("opening_width", "opening_height")
        if (parsed := formula(getattr(source, field), field)) is not None
    )
    if source.opening_depth is None:
        # Sizes written as "n x 3" leave depth out. Read them as one block deep,
        # as fixed doors without a stored opening depth are.
        opening += (Polynomial.constant(parser.variables, Fraction(1)),)
    elif (depth := formula(source.opening_depth, "opening_depth")) is not None:
        opening += (depth,)
    opening_stated = source.opening_width is not None and source.opening_height is not None
    inverse = None
    try:
        inverse = _inverse(opening, len(parser.variables))
    except ExpressionError as error:
        diagnostics.append(ExpansionDiagnostic(f"{prefix}.opening", "ambiguous_mapping", str(error)))
    domain = None
    domain_valid = True
    if source.domain is not None:
        try:
            domain = parser.predicate(source.domain)
        except ExpressionError as error:
            domain_valid = False
            diagnostics.append(ExpansionDiagnostic(f"{prefix}.domain", "unsupported", str(error)))
    volume = formula(source.volume, "volume")
    variants: dict[str | None, list[TimingFormula | None]] = {}
    seen: set[tuple[str | None, int]] = set()
    for i, timing in enumerate(source.timings):
        field = f"timings[{i}]"
        slot = _TIMING_METHODS.index(timing.method)
        empty_slots: list[TimingFormula | None] = [None] * 6
        slots = variants.setdefault(timing.variant, empty_slots)
        key = (timing.variant, slot)
        if key in seen:
            slots[slot] = None
            diagnostics.append(
                ExpansionDiagnostic(f"{prefix}.{field}", "duplicate", "Conflicting timing slot in one variant.")
            )
            continue
        seen.add(key)
        timing_formula = formula(timing.expression, field)
        if timing.unit not in _UNIT_SCALES:
            diagnostics.append(ExpansionDiagnostic(f"{prefix}.{field}.unit", "missing", "Timing unit is unknown."))
        elif timing_formula is not None:
            try:
                slots[slot] = TimingFormula(
                    scale_formula(timing_formula, Fraction(_UNIT_SCALES[timing.unit])), f"{prefix}.{field}.expression"
                )
            except ExpressionError as error:
                diagnostics.append(ExpansionDiagnostic(f"{prefix}.{field}", "unsupported", str(error)))
    return NormalizedBranch(
        index,
        source,
        opening,
        opening_stated,
        inverse,
        domain,
        domain_valid,
        volume,
        tuple(tuple(slots) for slots in variants.values()),
        tuple(diagnostics),
    )


def _contains(parameter: ExpansionParameter, value: int) -> bool | None:
    """Use known exclusions even when the rest of a domain is incomplete."""
    if parameter.maximum is not None and value > parameter.maximum:
        return False
    if parameter.minimum is None:
        return None
    if value < parameter.minimum:
        return False
    if value == parameter.minimum:
        return True
    if parameter.initial_steps is None:
        return None
    start = parameter.minimum
    for step in parameter.initial_steps:
        start += step
        if value < start:
            return False
        if value == start:
            return True if parameter.maximum_kind != "unknown" else None
    if parameter.repeating_steps is None:
        return None
    remainder = (value - start) % sum(parameter.repeating_steps)
    offset = 0
    supported = remainder == 0
    for step in parameter.repeating_steps:
        offset += step
        supported |= remainder == offset
    if not supported:
        return False
    return True if parameter.maximum_kind != "unknown" else None


def _evaluate_branch(
    expansion: NormalizedExpansion, branch: NormalizedBranch, opening: tuple[int, int, int]
) -> PointConfiguration:
    if branch.inverse is None:
        status = PointStatus.UNRESOLVED if branch.opening_stated else PointStatus.UNMAPPED
        return PointConfiguration(branch.index, status, diagnostics=branch.diagnostics)
    parameters = tuple(
        sum((coefficient * value for coefficient, value in zip(row, (*opening, 1), strict=True)), Fraction(0))
        for row in branch.inverse
    )
    if any(value.denominator != 1 for value in parameters):
        return PointConfiguration(branch.index, PointStatus.OUTSIDE_DOMAIN)
    values = tuple(value.numerator for value in parameters)
    if tuple(formula.evaluate(values) for formula in branch.opening) != opening:
        return PointConfiguration(branch.index, PointStatus.OUTSIDE_DOMAIN)
    assignments = tuple(
        (parameter.name, value) for parameter, value in zip(expansion.source.parameters, values, strict=True)
    )
    diagnostics = list(branch.diagnostics)
    membership = [
        _contains(parameter, value) for parameter, value in zip(expansion.source.parameters, values, strict=True)
    ]
    if False in membership:
        return PointConfiguration(branch.index, PointStatus.OUTSIDE_DOMAIN, assignments)
    domain_valid = branch.domain_valid
    if branch.domain is not None:
        try:
            if not branch.domain.evaluate(values):
                return PointConfiguration(branch.index, PointStatus.OUTSIDE_DOMAIN, assignments)
        except ExpressionError as error:
            domain_valid = False
            diagnostics.append(ExpansionDiagnostic(f"branches[{branch.index}].domain", "invalid_value", str(error)))
    for index, supported in enumerate(membership):
        if supported is None:
            diagnostics.append(
                ExpansionDiagnostic(
                    f"parameters[{index}]",
                    "incomplete_domain",
                    "Bounds or the layer progression do not establish this size.",
                )
            )
    if None in membership or not domain_valid:
        return PointConfiguration(branch.index, PointStatus.UNRESOLVED, assignments, diagnostics=tuple(diagnostics))

    def measurement(formula: Formula | None, path: str, minimum: int | None) -> int | None:
        if formula is None:
            return None
        value = formula.evaluate(values)
        if value.denominator != 1 or (minimum is not None and value < minimum):
            diagnostics.append(
                ExpansionDiagnostic(path, "invalid_value", "Measurement must be integral and within its allowed range.")
            )
            return None
        return value.numerator

    prefix = f"branches[{branch.index}]"
    volume = measurement(branch.volume, f"{prefix}.volume", 1)
    timings = tuple(
        tuple(
            measurement(timing.expression, timing.source_path, 0 if slot < 4 else None) if timing is not None else None
            for slot, timing in enumerate(variants)
        )
        for variants in branch.timings
    )
    return PointConfiguration(branch.index, PointStatus.SUPPORTED, assignments, volume, timings, tuple(diagnostics))
