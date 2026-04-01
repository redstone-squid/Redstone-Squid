import pytest

from squid.utils import parse_time_string, parse_version_string


@pytest.mark.parametrize(
    ("time_string", "expected"),
    [
        ("1.5s", 30),  # 1.5 seconds = 30 ticks
        ("30", 600),  # 30 is assumed to be seconds
        ("~2s", 40),  # 2s = 40 ticks
        ("invalid", None),  # Invalid format
        (None, None),  # None input
        ("-1", -20),
        ("0.055s", 1),  # Extra precision is ignored
    ],
)
def test_parse_time_string(time_string: str | None, expected: int | None):
    """Test time string parsing with various formats."""
    result = parse_time_string(time_string)
    assert result == expected


@pytest.mark.parametrize(
    ("version_string", "expected"),
    [
        ("Java 26.0", ("Java", 26, 0, 0)),
        ("Bedrock 26.1", ("Bedrock", 26, 1, 0)),
        ("26.1.3", ("Java", 26, 1, 3)),
    ],
)
def test_parse_version_string(version_string: str, expected: tuple[str, int, int, int]):
    """Test version string parsing supports both year and patch release formats."""
    result = parse_version_string(version_string)
    assert result == expected
