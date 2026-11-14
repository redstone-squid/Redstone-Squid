from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid.builds.domain.expansion_math import ExpressionError, ExpressionParser, substitute_formula


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
        ExpressionParser(("n",)).formula(source)


def test_domain_comparisons_and_congruences() -> None:
    predicate = ExpressionParser(("n",)).predicate("(5 < n <= 8 or n > 15); not n % 2 = 1")
    assert [n for n in range(1, 20) if predicate.evaluate((n,))] == [6, 8, 16, 18]


def test_semicolon_joins_whole_conditions() -> None:
    # Python's "and" binds tighter than "or"; a semicolon must not.
    predicate = ExpressionParser(("n",)).predicate("n < 5 or n > 10; n % 2 = 0")
    assert [n for n in range(1, 15) if predicate.evaluate((n,))] == [2, 4, 12, 14]
    with pytest.raises(ExpressionError):
        ExpressionParser(("n",)).predicate("(n < 5; n > 2)")


def test_unknown_identifier_is_not_split_into_parameters() -> None:
    with pytest.raises(ExpressionError):
        ExpressionParser(("x", "y")).polynomial("xy")


@given(st.integers(-(10**40), 10**40), st.integers(1, 100))
def test_rounding_is_exact_for_signed_large_rationals(n: int, denominator: int) -> None:
    parser = ExpressionParser(("n",))
    assert parser.formula(f"floor(n/{denominator})").evaluate((n,)) == n // denominator
    assert parser.formula(f"ceil(n/{denominator})").evaluate((n,)) == -(-n // denominator)
    assert parser.formula(f"ceiling(n/{denominator})").evaluate((n,)) == -(-n // denominator)


def test_nested_rounding_and_implicit_products() -> None:
    parser = ExpressionParser(("n",))
    assert parser.formula("2ceil(floor(n/2)/3)+floor(-1.5)").evaluate((15,)) == 4
    assert parser.formula("ceil(n/2)floor(n/2)").evaluate((5,)) == 6
    assert parser.polynomial("floor(n+1)").evaluate((4,)) == 5
    predicate = parser.predicate("floor(n/2) % 2 = 0 and ceil(n/3) <= 3")
    assert [n for n in range(1, 11) if predicate.evaluate((n,))] == [1, 4, 5, 8, 9]


@pytest.mark.parametrize(
    "source",
    [
        "round(n/2)",
        "floor()",
        "ceil(n,n)",
        "floor(n, mode=1)",
        "floor(n/0)",
        "ceil(n)/floor(n/2)",
        "ceil(n/2)**3*floor(n/3)",
    ],
)
def test_rounding_does_not_allow_arbitrary_calls_or_unbounded_arithmetic(source: str) -> None:
    with pytest.raises(ExpressionError):
        ExpressionParser(("n",)).formula(source)


@pytest.mark.parametrize("name", ["floor", "ceil", "ceiling"])
def test_rounding_function_names_are_reserved(name: str) -> None:
    with pytest.raises(ExpressionError):
        ExpressionParser((name,))


@pytest.mark.parametrize("source", ["(x+3)(y+3)", "ceil(x/2)+floor(y/3)", "floor(ceil(x/2)/3)*y"])
def test_substitution_preserves_polynomials_and_rounding(source: str) -> None:
    original = ExpressionParser(("x", "y")).formula(source)
    target = ExpressionParser(("width",))
    translated = substitute_formula(
        original, (target.polynomial("2width+1"), target.polynomial("3")), variables=("width",)
    )
    for width in range(-10, 11):
        assert translated.evaluate((width,)) == original.evaluate((2 * width + 1, 3))
