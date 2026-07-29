"""Build value parsing tests."""

import pytest

from squid.builds.domain import parse_time_string


@pytest.mark.parametrize(
    ("time_string", "expected"),
    [
        ("1.5s", 30),
        ("30", 600),
        ("~2s", 40),
        ("invalid", None),
        (None, None),
        ("-1", -20),
        ("0.055s", 1),
    ],
)
def test_parse_time_string(time_string: str | None, expected: int | None) -> None:
    assert parse_time_string(time_string) == expected
