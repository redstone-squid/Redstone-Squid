from hypothesis import given
from hypothesis import strategies as st

from squid.services.versions import Edition, MinecraftVersion, parse_version_string


@given(
    edition=st.sampled_from(("Java", "Bedrock")),
    major=st.integers(min_value=0, max_value=32_767),
    minor=st.integers(min_value=0, max_value=32_767),
    patch=st.integers(min_value=0, max_value=32_767),
)
def test_canonical_version_string_round_trips(
    edition: Edition,
    major: int,
    minor: int,
    patch: int,
) -> None:
    version = MinecraftVersion(edition, major, minor, patch)

    assert parse_version_string(str(version)) == (edition, major, minor, patch)
