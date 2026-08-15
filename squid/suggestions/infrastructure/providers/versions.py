"""Suggestion provider over recognized Minecraft versions."""

from collections.abc import Sequence
from typing import Protocol

from squid.suggestions.application import Candidate, candidate
from squid.suggestions.domain import SuggestionRequest
from squid.suggestions.infrastructure.cache import TtlCache
from squid.versions.domain import MinecraftVersion


class CanonicalMinecraftVersions(Protocol):
    """Read canonical versions recognized by build persistence."""

    async def list_all(self) -> Sequence[MinecraftVersion]: ...


class VersionProvider:
    """Suggest canonical version strings, newest first."""

    def __init__(self, versions: CanonicalMinecraftVersions) -> None:
        self._versions = versions
        self._cache = TtlCache[None, tuple[Candidate, ...]](self._load)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return await self._cache.get(None)

    async def _load(self, _key: None) -> tuple[Candidate, ...]:
        # Newest first, because an empty query should offer the versions people actually submit
        # against rather than the oldest ones the catalogue happens to start with.
        names = sorted({str(version) for version in await self._versions.list_all()}, key=_sort_key)
        return tuple(candidate(name, kind="version") for name in names)


class VersionIdReader(Protocol):
    """Read version database ids with their display names."""

    async def version_ids(self) -> Sequence[tuple[int, str]]: ...


class VersionIdProvider:
    """Suggest versions by name while submitting the database id the command expects."""

    def __init__(self, reader: VersionIdReader) -> None:
        self._reader = reader
        self._cache = TtlCache[None, tuple[Candidate, ...]](self._load)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return await self._cache.get(None)

    async def _load(self, _key: None) -> tuple[Candidate, ...]:
        return tuple(
            candidate(str(version_id), label=name, kind="version", terms=(name, str(version_id)))
            for version_id, name in await self._reader.version_ids()
        )


def _sort_key(value: str) -> tuple[bool, tuple[int, ...]]:
    """Order Java before Bedrock, and newer releases before older ones."""
    edition, _, version = value.partition(" ")
    return edition != "Java", tuple(-int(part) for part in version.split(".") if part.isdigit())
