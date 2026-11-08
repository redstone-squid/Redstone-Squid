import operator
from functools import reduce

from squid.core.scores import round_score


def naive_sum(values: list[float]) -> float:
    # Python's sum() compensates rounding error; PostgreSQL's float SUM, like this, does not.
    return reduce(operator.add, values, 0.0)


def test_ten_tenths_reach_one() -> None:
    raw = naive_sum([0.1] * 10)
    assert raw < 1
    assert round_score(raw) == 1


def test_summation_order_does_not_change_the_score() -> None:
    weights = [0.1, 0.2, 0.3]
    assert naive_sum(weights) != naive_sum(weights[::-1])
    assert round_score(naive_sum(weights)) == round_score(naive_sum(weights[::-1]))
