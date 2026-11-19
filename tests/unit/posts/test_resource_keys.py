"""Starboard resource keys share one validated wire representation."""

import pytest

from squid.core.posts.domain import StarboardEntryKey


def test_starboard_key_round_trip() -> None:
    key = StarboardEntryKey(12, 345)
    assert str(key) == "12:345"
    assert StarboardEntryKey.parse(str(key)) == key


@pytest.mark.parametrize("value", ["12", "1:2:3", "board:123", "0:12", "12:-1"])
def test_rejects_invalid_starboard_keys(value: str) -> None:
    with pytest.raises(ValueError, match=r"unpack|invalid literal|positive"):
        StarboardEntryKey.parse(value)
