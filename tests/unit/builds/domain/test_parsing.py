"""Build value parsing tests."""

import json
from pathlib import Path

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


_DURATION_FIXTURE = Path(__file__).resolve().parents[4] / "contracts" / "fixtures" / "duration-cases.json"
_DURATION_CASES = json.loads(_DURATION_FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("time_string", "expected"),
    [(case["input"], case["ticks"]) for case in _DURATION_CASES["core"] + _DURATION_CASES["python_only"]]
    + [(case["input"], case["python_ticks"]) for case in _DURATION_CASES["client_rejects"]],
)
def test_parse_time_string_matches_shared_duration_fixture(time_string: str, expected: int | None) -> None:
    """The fixture's `core` section is the cross-surface duration language; drift here breaks every client."""
    assert parse_time_string(time_string) == expected
