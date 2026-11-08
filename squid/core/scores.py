"""Rounding for sums of fractional vote weights."""

SCORE_DECIMALS = 6
"""Decimal places kept in a weighted score.

Admins type role multipliers freely, so a weight such as 0.1 is allowed. Ten such votes sum to
0.9999999999999999 in binary floating point and would miss a threshold of 1, and summing the same
weights in another order can change the last digit. Rounding every computed score makes threshold
checks and "has the score changed" comparisons depend on the weights alone.
"""


def round_score(value: float) -> float:
    """Round a sum of vote weights to `SCORE_DECIMALS` places."""
    return round(value, SCORE_DECIMALS)
