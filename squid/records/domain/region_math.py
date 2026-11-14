"""Exact one-variable arithmetic for bounded record-region analysis.

Rounding becomes polynomial formulas on residue classes. Polynomial signs are
constant on integer intervals found by finite differences and binary search;
no floating-point roots or enumeration of opening sizes is needed.
"""

from dataclasses import dataclass
from fractions import Fraction
from math import comb, lcm
from typing import Literal

from squid.builds.domain.expansion_math import ArithmeticFormula, Formula, Polynomial, RoundedFormula


class RegionArithmeticError(ValueError):
    """The requested arithmetic is outside the supported one-variable grammar."""


class RegionLimitError(RegionArithmeticError):
    """A deterministic work or representation limit was reached."""


@dataclass(slots=True)
class ArithmeticBudget:
    """Shared work allowance across all comparisons in one analysis."""

    remaining: int = 50_000

    def spend(self) -> None:
        self.remaining -= 1
        if self.remaining < 0:
            msg = "Region arithmetic exhausted its work allowance."
            raise RegionLimitError(msg)


@dataclass(frozen=True, slots=True)
class SignInterval:
    """Inclusive integer endpoints; None is an unbounded upper endpoint."""

    minimum: int
    maximum: int | None
    sign: Literal[-1, 0, 1]


@dataclass(frozen=True, slots=True)
class FormulaPhases:
    """Phase r evaluates at k when the original coordinate is period*k+r."""

    period: int
    formulas: tuple[Polynomial, ...]


def polynomial_phases(formula: Formula, *, max_period: int = 128) -> FormulaPhases:
    """Lower nested rounding to residue polynomials or raise an explicit limit.

    Nonconstant coefficients in each result are integral. Thus a nonintegral
    constant makes that entire phase nonintegral. The period need not be minimal.
    """
    _check_formula(formula)
    period = 1
    while period <= max_period:
        formulas: list[Polynomial] = []
        try:
            for residue in range(period):
                polynomial = _on_progression(formula, period, residue)
                _require_integral_variation(polynomial)
                formulas.append(polynomial)
        except _RefinePeriod as refinement:
            period *= refinement.divisor
        else:
            return FormulaPhases(period, tuple(formulas))
    msg = "Rounding or integral measurements require too many residue classes."
    raise RegionLimitError(msg)


class _RefinePeriod(RegionArithmeticError):
    def __init__(self, divisor: int) -> None:
        self.divisor = divisor
        super().__init__("A finer residue partition is required.")


def on_progression(formula: Formula, period: int, residue: int) -> Polynomial:
    """Substitute n=period*k+residue, preserving every rounding operation.

    Call with a multiple of polynomial_phases(formula).period. A smaller period
    can fail when a rounded operand still has fractional varying coefficients.
    """
    _check_formula(formula)
    if period < 1 or not 0 <= residue < period:
        msg = "A progression requires a positive period and a residue in that period."
        raise RegionArithmeticError(msg)
    return _on_progression(formula, period, residue)


def _on_progression(formula: Formula, period: int, residue: int) -> Polynomial:
    if isinstance(formula, Polynomial):
        terms: dict[tuple[int, ...], Fraction] = {}
        for powers, value in formula.terms:
            degree = powers[0]
            for power in range(degree + 1):
                term = value * comb(degree, power) * period**power * residue ** (degree - power)
                terms[(power,)] = terms.get((power,), Fraction(0)) + term
        return Polynomial.create(formula.variables, terms)
    if isinstance(formula, ArithmeticFormula):
        left = _on_progression(formula.left, period, residue)
        right = _on_progression(formula.right, period, residue)
        return left.add(right) if formula.operator == "add" else left.multiply(right)
    operand = _on_progression(formula.operand, period, residue)
    _require_integral_variation(operand)
    terms = dict(operand.terms)
    constant = terms.get((0,), Fraction(0))
    terms[(0,)] = Fraction(
        constant.numerator // constant.denominator
        if formula.direction == "floor"
        else -(-constant.numerator // constant.denominator)
    )
    return Polynomial.create(operand.variables, terms)


def integer_sign_intervals(
    polynomial: Polynomial,
    *,
    minimum: int = 1,
    maximum: int | None = None,
    budget: ArithmeticBudget | None = None,
) -> tuple[SignInterval, ...]:
    """Partition an integer domain into exact negative, zero and positive intervals.

    A polynomial's finite difference has lower degree. Its sign intervals split
    the original sequence into monotone runs, whose zero crossings are located
    by binary search. A rational coefficient bound closes the unbounded tail.
    """
    _check_formula(polynomial)
    work = budget if budget is not None else ArithmeticBudget()
    coefficients = tuple(dict(polynomial.terms).get((power,), Fraction(0)) for power in range(polynomial.degree + 1))
    if maximum is not None:
        return tuple(_finite_signs(coefficients, minimum, maximum, work))
    # Every real root is smaller in magnitude than 1 + max |a_i/a_degree|.
    ratio = max((abs(value / coefficients[-1]) for value in coefficients[:-1]), default=Fraction(0))
    bound = 2 + -(-ratio.numerator // ratio.denominator)
    end = max(minimum, bound)
    intervals = _finite_signs(coefficients, minimum, end, work)
    _append(intervals, SignInterval(end + 1, None, _sign(_evaluate(coefficients, end + 1, work))))
    return tuple(intervals)


def _check_formula(formula: Formula) -> None:
    if len(formula.variables) != 1 or formula.degree > 3:
        msg = "Region arithmetic requires one variable and degree at most three."
        raise RegionArithmeticError(msg)
    if isinstance(formula, Polynomial):
        if any(
            max(abs(value.numerator).bit_length(), value.denominator.bit_length()) > 256 for _, value in formula.terms
        ):
            msg = "Region coefficients exceed the supported bit limit."
            raise RegionLimitError(msg)
    elif isinstance(formula, RoundedFormula):
        _check_formula(formula.operand)
    else:
        _check_formula(formula.left)
        _check_formula(formula.right)


def _evaluate(coefficients: tuple[Fraction, ...], value: int, budget: ArithmeticBudget) -> Fraction:
    budget.spend()
    result = Fraction(0)
    for coefficient in reversed(coefficients):
        result = result * value + coefficient
    return result


def _sign(value: Fraction) -> Literal[-1, 0, 1]:
    return -1 if value < 0 else 1 if value > 0 else 0


def _append(intervals: list[SignInterval], interval: SignInterval) -> None:
    if intervals and intervals[-1].sign == interval.sign and intervals[-1].maximum == interval.minimum - 1:
        previous = intervals.pop()
        interval = SignInterval(previous.minimum, interval.maximum, interval.sign)
    intervals.append(interval)


def _finite_signs(
    coefficients: tuple[Fraction, ...], minimum: int, maximum: int, budget: ArithmeticBudget
) -> list[SignInterval]:
    if minimum > maximum:
        return []
    if len(coefficients) == 1 or minimum == maximum:
        return [SignInterval(minimum, maximum, _sign(_evaluate(coefficients, minimum, budget)))]
    difference = tuple(
        sum((coefficients[j] * comb(j, power) for j in range(power + 1, len(coefficients))), Fraction(0))
        for power in range(len(coefficients) - 1)
    )
    intervals: list[SignInterval] = []
    for monotone in _finite_signs(difference, minimum, maximum - 1, budget):
        assert monotone.maximum is not None
        if monotone.sign == 0:
            _append(
                intervals,
                SignInterval(
                    monotone.minimum, monotone.maximum, _sign(_evaluate(coefficients, monotone.minimum, budget))
                ),
            )
            continue

        first_zero = _crossing(coefficients, monotone, budget, strict=False)
        first_positive = _crossing(coefficients, monotone, budget, strict=True)
        for low, high, sign in (
            (monotone.minimum, first_zero - 1, -monotone.sign),
            (first_zero, first_positive - 1, 0),
            (first_positive, monotone.maximum, monotone.sign),
        ):
            if low <= high:
                _append(intervals, SignInterval(low, high, _sign(Fraction(sign))))
    _append(intervals, SignInterval(maximum, maximum, _sign(_evaluate(coefficients, maximum, budget))))
    return intervals


def _require_integral_variation(polynomial: Polynomial) -> None:
    divisor = lcm(*(value.denominator for powers, value in polynomial.terms if powers[0]))
    if divisor != 1:
        raise _RefinePeriod(divisor)


def _crossing(
    coefficients: tuple[Fraction, ...], interval: SignInterval, budget: ArithmeticBudget, *, strict: bool
) -> int:
    assert interval.maximum is not None
    low, high = interval.minimum, interval.maximum + 1
    while low < high:
        middle = (low + high) // 2
        # Reverse decreasing runs so both searches use an increasing predicate.
        value = _evaluate(coefficients, middle, budget) * interval.sign
        crossed = value > 0 if strict else value >= 0
        if crossed:
            high = middle
        else:
            low = middle + 1
    return low
