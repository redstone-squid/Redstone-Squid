"""Shared exponential retry delays and their cap."""

from datetime import timedelta

import pytest

from squid.persistence.queue import BASE_RETRY_DELAY, MAX_RETRY_DELAY, retry_delay


def test_the_backoff_doubles_from_the_base_delay() -> None:
    assert retry_delay(1) == BASE_RETRY_DELAY
    assert retry_delay(2) == BASE_RETRY_DELAY * 2
    assert retry_delay(3) == BASE_RETRY_DELAY * 4


def test_the_backoff_is_capped_so_a_stuck_job_still_retries_hourly() -> None:
    assert retry_delay(12) == MAX_RETRY_DELAY
    # A queue with `max_attempts=None` reaches counts like this. Unclamped, the
    # exponent leaves `timedelta`'s range and raises `OverflowError` rather than
    # backing off.
    assert retry_delay(50) == MAX_RETRY_DELAY
    assert retry_delay(10_000) == MAX_RETRY_DELAY


def test_a_zeroth_attempt_halves_the_base_delay() -> None:
    """Zero attempts uses exponent -1; this helper does not clamp the lower bound."""
    assert retry_delay(0) == timedelta(seconds=7.5)


@pytest.mark.parametrize("attempts", range(1, 13))
def test_the_backoff_never_shrinks(attempts: int) -> None:
    assert retry_delay(attempts) >= retry_delay(attempts - 1)
