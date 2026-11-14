"""Exact partitions agree with direct arithmetic, including endpoints and tails."""

from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from squid.builds.domain.expansion_math import ExpressionParser, Polynomial
from squid.records.domain.region_math import (
    ArithmeticBudget,
    RegionArithmeticError,
    RegionLimitError,
    SignInterval,
    integer_sign_intervals,
    polynomial_phases,
)

PARSER = ExpressionParser(("n",))


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("0", (SignInterval(1, None, 0),)),
        ("-2", (SignInterval(1, None, -1),)),
        ("n-6", (SignInterval(1, 5, -1), SignInterval(6, 6, 0), SignInterval(7, None, 1))),
        ("(n-6)^2", (SignInterval(1, 5, 1), SignInterval(6, 6, 0), SignInterval(7, None, 1))),
        ("2n-11", (SignInterval(1, 5, -1), SignInterval(6, None, 1))),
        (
            "(n-5)(n-8)(n-15)",
            (
                SignInterval(1, 4, -1),
                SignInterval(5, 5, 0),
                SignInterval(6, 7, 1),
                SignInterval(8, 8, 0),
                SignInterval(9, 14, -1),
                SignInterval(15, 15, 0),
                SignInterval(16, None, 1),
            ),
        ),
        ("n^2-2", (SignInterval(1, 1, -1), SignInterval(2, None, 1))),
        ("-n^3+2", (SignInterval(1, 1, 1), SignInterval(2, None, -1))),
    ],
)
def test_exact_integer_crossings(expression: str, expected: tuple[SignInterval, ...]) -> None:
    assert integer_sign_intervals(PARSER.polynomial(expression)) == expected


@given(st.lists(st.fractions(min_value=-20, max_value=20, max_denominator=12), min_size=1, max_size=4))
@settings(max_examples=150)
def test_sign_partition_matches_direct_evaluation(coefficients: list[Fraction]) -> None:
    polynomial = Polynomial.create(("n",), {(i,): value for i, value in enumerate(coefficients)})
    intervals = integer_sign_intervals(polynomial, minimum=-40, maximum=50)
    represented = {}
    for interval in intervals:
        assert interval.maximum is not None
        for value in range(interval.minimum, interval.maximum + 1):
            assert value not in represented
            represented[value] = interval.sign
    assert set(represented) == set(range(-40, 51))
    for value, sign in represented.items():
        result = polynomial.evaluate((value,))
        assert sign == (result > 0) - (result < 0)
    tail = integer_sign_intervals(polynomial, minimum=-40)
    for value in (*range(-40, 51), 10**6):
        interval = next(i for i in tail if i.minimum <= value and (i.maximum is None or value <= i.maximum))
        result = polynomial.evaluate((value,))
        assert interval.sign == (result > 0) - (result < 0)


def test_huge_crossover_uses_bounded_search() -> None:
    root = 10**60
    budget = ArithmeticBudget(5000)
    polynomial = PARSER.polynomial(f"n-{root}")
    assert integer_sign_intervals(polynomial, budget=budget) == (
        SignInterval(1, root - 1, -1),
        SignInterval(root, root, 0),
        SignInterval(root + 1, None, 1),
    )
    assert budget.remaining > 4000


@pytest.mark.parametrize(
    "expression",
    [
        "ceil(n/2)",
        "floor(-n/3)",
        "ceil(floor(n/2)/3)",
        "floor(n^2/4)",
        "ceil(n/2)*floor((n+1)/3)+n/2",
        "floor(ceil(n/2)/2)",
        "ceil(n/2)/3+floor(n/2)/5",
        "n/2+1/3",
    ],
)
def test_rounding_phases_preserve_exact_values(expression: str) -> None:
    formula = PARSER.formula(expression)
    phases = polynomial_phases(formula)
    for n in range(-100, 101):
        k, residue = divmod(n, phases.period)
        assert phases.formulas[residue].evaluate((k,)) == formula.evaluate((n,))
        assert all(value.denominator == 1 for powers, value in phases.formulas[residue].terms if powers[0])


def test_limits_are_explicit() -> None:
    with pytest.raises(RegionLimitError, match="residue"):
        polynomial_phases(PARSER.formula("ceil(n/129)"))
    with pytest.raises(RegionLimitError, match="work allowance"):
        integer_sign_intervals(PARSER.polynomial("n^3-10000000"), budget=ArithmeticBudget(1))
    with pytest.raises(RegionArithmeticError, match="one variable"):
        integer_sign_intervals(ExpressionParser(("x", "y")).polynomial("x+y"))


def test_empty_and_singleton_queries() -> None:
    assert integer_sign_intervals(PARSER.polynomial("n^2"), minimum=3, maximum=2) == ()
    assert integer_sign_intervals(PARSER.polynomial("n^2"), minimum=0, maximum=0) == (SignInterval(0, 0, 0),)
