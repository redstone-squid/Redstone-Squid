"""Content identities distinguish boundaries even when text contains delimiters."""

import pytest

from squid_ui.planning.cursors import content_fingerprint


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ([], [""]),
        (["a\0b", "c"], ["a", "b\0c"]),
        (["a", "b"], ["b", "a"]),
    ],
)
def test_distinct_content_has_distinct_identity(left: list[str], right: list[str]) -> None:
    assert content_fingerprint(left) != content_fingerprint(right)
