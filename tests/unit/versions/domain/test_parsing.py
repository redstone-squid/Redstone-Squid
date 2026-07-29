"""Version value parsing tests."""

import pytest

from squid.versions.domain import parse_version_string


@pytest.mark.parametrize(
    ("version_string", "expected"),
    [
        ("Java 26.0", ("Java", 26, 0, 0)),
        ("Bedrock 26.1", ("Bedrock", 26, 1, 0)),
        ("bedrock 26.1", ("Bedrock", 26, 1, 0)),
        ("26.1.3", ("Java", 26, 1, 3)),
    ],
)
def test_parse_version_string(version_string: str, expected: tuple[str, int, int, int]) -> None:
    assert parse_version_string(version_string) == expected
