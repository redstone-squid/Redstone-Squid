import pytest

from squid.utils import parse_time_string


@pytest.mark.parametrize(
    "time_string, expected",
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
