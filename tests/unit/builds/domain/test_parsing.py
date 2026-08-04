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
        ("2 seconds", 40),
        ("~ 1.05 sec", 21),
        ("21gt", 21),
        ("21 gt", 21),
        ("16 ticks", 16),
        ("1 tick", 1),
        ("40 game ticks", 40),
        ("40game-ticks", 40),
        ("3rt", 6),
        ("3 redstone ticks", 6),
        (".5s", 10),
        ("  8S  ", 160),
        ("", None),
        ("8 minutes", None),
        ("8s 40ms", None),
        ("1.16+", None),
    ],
)
def test_parse_time_string(time_string: str | None, expected: int | None) -> None:
    assert parse_time_string(time_string) == expected
