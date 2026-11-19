"""Conversion releases retain distinct identities when formats share a number."""

from squid.core.suggestions.domain import SuggestionRequest
from squid.core.suggestions.infrastructure.providers.versions import ConversionVersionProvider


class ConversionVersions:
    async def conversion_versions(self) -> tuple[tuple[int, str], ...]:
        return ((3953, "Java 1.21.1"), (3953, "Java 1.21.0"))


async def test_conversion_release_identity_is_distinct_from_world_format_number() -> None:
    candidates = await ConversionVersionProvider(ConversionVersions()).candidates(
        SuggestionRequest("conversion_versions")
    )
    assert [candidate.suggestion.value for candidate in candidates] == ["Java 1.21.1", "Java 1.21.0"]
