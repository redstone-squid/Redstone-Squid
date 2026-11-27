"""Suggestion provider over recognized Minecraft versions."""

from collections.abc import Sequence
from typing import Protocol

from squid.core.actors import ActingContext
from squid.core.suggestions.application import Candidate
from squid.core.suggestions.application.queries import SuggestionRepository
from squid.core.suggestions.application.registry import SuggestionSource
from squid.core.suggestions.domain import SourceKind, SuggestionRequest, ValueType
from squid.core.suggestions.infrastructure.cache import TtlCache
from squid.core.versions.domain import VersionEntry


class CanonicalMinecraftVersions(Protocol):
    """Read canonical versions recognized by build persistence."""

    async def list_all(self, *, context: ActingContext) -> Sequence[VersionEntry]: ...


class VersionProvider:
    """Suggest cached version names, Java before Bedrock and newest first within each edition."""

    def __init__(self, versions: CanonicalMinecraftVersions) -> None:
        self._versions = versions
        self._cache = TtlCache[None, tuple[Candidate, ...]](self._load)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return await self._cache.get(None)

    async def _load(self, _key: None) -> tuple[Candidate, ...]:
        # Newest first, because an empty query should offer the versions people actually submit
        # against rather than the oldest ones the catalogue happens to start with.
        names = sorted(
            {str(version) for version in await self._versions.list_all(context=ActingContext.current())}, key=_sort_key
        )
        return tuple(Candidate.create(name, kind="version") for name in names)


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
            Candidate.create(str(version_id), label=name, kind="version", terms=(name, str(version_id)))
            for version_id, name in await self._reader.version_ids()
        )


def _sort_key(value: str) -> tuple[bool, tuple[int, ...]]:
    """Order Java before Bedrock, and newer releases before older ones."""
    edition, _, version = value.partition(" ")
    return edition != "Java", tuple(-int(part) for part in version.split(".") if part.isdigit())


class ConversionVersionReader(Protocol):
    async def conversion_versions(self) -> Sequence[tuple[int, str]]: ...


class ConversionVersionProvider:
    """Offer mapped Java releases while retaining their world-format number."""

    def __init__(self, reader: ConversionVersionReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        return tuple(
            Candidate.create(name, label=name, kind="version")
            for _number, name in await self._reader.conversion_versions()
        )


def version_sources(
    versions: CanonicalMinecraftVersions, repository: SuggestionRepository
) -> tuple[SuggestionSource, ...]:
    return (
        SuggestionSource(
            id="approved_source_versions",
            provider=VersionProvider(versions),
            kind=SourceKind.ENUMERABLE,
            kind_label="version",
        ),
        SuggestionSource(
            id="conversion_versions",
            provider=ConversionVersionProvider(repository),
            kind=SourceKind.ENUMERABLE,
            kind_label="version",
        ),
        SuggestionSource(
            id="version_ids",
            provider=VersionIdProvider(repository),
            kind=SourceKind.ENUMERABLE,
            value_type=ValueType.INTEGER,
            kind_label="version",
        ),
    )
