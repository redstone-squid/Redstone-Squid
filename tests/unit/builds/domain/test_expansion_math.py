from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid.builds.domain.expansion_math import ExpressionError, ExpressionParser


@given(st.integers(1, 10000), st.integers(1, 10000), st.integers(1, 10000))
def test_product_matches_integer_arithmetic(x: int, y: int, z: int) -> None:
    formula = ExpressionParser(("x", "y", "z")).polynomial("(x+3)(y+3)(z+1)")
    assert formula.evaluate((x, y, z)) == (x + 3) * (y + 3) * (z + 1)


def test_canonical_terms_and_exact_decimals() -> None:
    parser = ExpressionParser(("n",))
    assert parser.polynomial("n+n+0.1").terms == parser.polynomial("2n+1/10").terms
    assert parser.polynomial("(n-1)^3").evaluate((4,)) == 27
    assert parser.polynomial("0.05n").evaluate((3,)) == Fraction(3, 20)


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os')",
        "n.__class__",
        "n[0]",
        "ceil(n/2)",
        "1/0",
        "n/n",
        "n**100",
        "n**-1",
        "n**3*n",
        "True",
        "xy",
        "1e10",
        "1" * 100,
        "(" * 200 + "n" + ")" * 200,
    ],
)
def test_reject_unsupported_or_unbounded_expressions(source: str) -> None:
    with pytest.raises(ExpressionError):
        ExpressionParser(("n",)).polynomial(source)


def test_domain_comparisons_and_congruences() -> None:
    predicate = ExpressionParser(("n",)).predicate("(5 < n <= 8 or n > 15); not n % 2 = 1")
    assert [n for n in range(1, 20) if predicate.evaluate((n,))] == [6, 8, 16, 18]


def test_unknown_identifier_is_not_split_into_parameters() -> None:
    with pytest.raises(ExpressionError):
        ExpressionParser(("x", "y")).polynomial("xy")
