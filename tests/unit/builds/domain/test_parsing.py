"""Build value parsing tests."""

import json
from pathlib import Path

import pytest

from squid.builds.domain import parse_time_string


@pytest.mark.parametrize(
    ("time_string", "expected"),
    [
        (None, None),
        ("40game-ticks", 40),
        ("8 minutes", 9600),
        ("~42 minutes", 50400),
        ("0.5min", 600),
        ("2 mins", 2400),
        ("9-gt", 9),
        ("9 - gt", 9),
        ("9--gt", None),
        ("9-", None),
        ("8s 40ms", None),
        ("4h 47m 30s", 345000),
        ("~3min25s", 4100),
        ("2 minutes and 35.5 seconds", 3110),
        ("94m 18s", 113160),
        ("1 hour", 72000),
        ("1m 1m", None),
        ("30s 1m", None),
        ("1s + 2s reset", None),
        ("1m and", None),
        ("1m and ", None),
        ("1m -2s", None),
        ("1m/2s", None),
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
    """Shared cases keep server parsing aligned with client duration inputs."""
    assert parse_time_string(time_string) == expected
