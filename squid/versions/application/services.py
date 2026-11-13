"""Minecraft version application services."""

import re

from squid.unit_of_work import UnitOfWorkFactory
from squid.versions.application.ports import VersionUnitOfWork
from squid.versions.domain import Edition, MinecraftVersion, parse_version_string
from squid.versions.errors import InvalidVersionError, VersionCatalogUnavailableError


class VersionService:
    """Parse, store, and list recognized Minecraft versions."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[VersionUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def add(self, version_string: str, *, edition: Edition | None = None) -> MinecraftVersion:
        """Parse and store a version, using `edition` to override the parsed edition."""
        parsed_edition, major, minor, patch = parse_version_string(version_string)
        version = MinecraftVersion(edition or parsed_edition, major, minor, patch)
        async with self._uow_factory() as uow:
            stored = await uow.versions.add(version)
            await uow.commit()
            return stored

    async def data_version_for(self, version_label: str) -> int | None:
        """Return the saved-data version, or None for an invalid, unknown, or unmapped release."""
        async with self._uow_factory() as uow:
            return await uow.versions.data_version_for(version_label)

    async def label_for_data_version(self, data_version: int) -> str | None:
        """Return the earliest matching catalogue release, or None if no release matches."""
        async with self._uow_factory() as uow:
            return await uow.versions.label_for_data_version(data_version)

    async def list_versions(self, edition: Edition) -> list[MinecraftVersion]:
        """Return catalog versions for one edition, from oldest to newest."""
        async with self._uow_factory() as uow:
            return await uow.versions.list(edition)

    async def list_all(self) -> list[MinecraftVersion]:
        """List Java versions followed by Bedrock versions, oldest first within each edition."""
        async with self._uow_factory() as uow:
            java = await uow.versions.list("Java")
            bedrock = await uow.versions.list("Bedrock")
            return [*java, *bedrock]

    async def list_display(self, edition: Edition, *, limit: int | None = None) -> list[str]:
        """Return oldest-first display strings, optionally sliced to `limit`."""
        versions = await self.list_versions(edition)
        return [str(version) for version in (versions if limit is None else versions[:limit])]

    async def newest(self, edition: Edition) -> str:
        """Return the newest recognized version as a display string.

        Raise VersionCatalogUnavailableError if the edition has no catalog entries.
        """
        versions = await self.list_versions(edition)
        if not versions:
            raise VersionCatalogUnavailableError(edition)
        return str(versions[-1])

    async def resolve_spec(self, version_spec: str) -> list[str]:
        """Resolve comma-separated version selections against the catalog.

        `1.19` selects every known patch in that release. `1.19 - 1.20` includes
        both endpoints and every known patch of 1.20. Tildes and typographic dashes
        between numeric bounds are also accepted. `1.21+` selects 1.21.0 and
        later releases. A full version such as `1.21.4` selects only that entry.

        Prefix the selection with `Java` or `Bedrock`; Java is the default.
        Unlike parse_version_string, these edition names are case-sensitive.
        Results follow selection order and catalog order within each selection.
        Overlapping selections can produce duplicates; unknown versions contribute
        no entries. Malformed numeric input can raise ValueError.
        """
        edition = self._edition_from_spec(version_spec)
        normalized_spec = version_spec.replace("Java", "").replace("Bedrock", "").strip()
        normalized_spec = re.sub(r"(?<=\d)\s*[~\uff5e\u2013\u2014]\s*(?=\d)", " - ", normalized_spec)
        versions = await self.list_versions(edition)
        available = [(version.major, version.minor, version.patch) for version in versions]
        resolved: list[tuple[int, int, int]] = []

        for part in (part.strip() for part in normalized_spec.split(",")):
            if "-" in part:
                start_text, end_text = self._range_bounds(part)
                start = self._parse_version_numbers(start_text)
                end = self._parse_range_end(end_text, available)
                resolved.extend(version for version in available if start <= version <= end)
            elif part.endswith("+"):
                start = self._parse_version_numbers(part[:-1].strip())
                resolved.extend(version for version in available if version >= start)
            else:
                numbers = tuple(map(int, part.split(".")))
                if len(numbers) == 2:
                    resolved.extend(
                        version for version in available if version[0] == numbers[0] and version[1] == numbers[1]
                    )
                elif len(numbers) == 3 and numbers in available:
                    resolved.append(numbers)

        return [f"{edition} {major}.{minor}.{patch}" for major, minor, patch in resolved]

    @staticmethod
    def _edition_from_spec(version_spec: str) -> Edition:
        has_bedrock = "Bedrock" in version_spec
        has_java = "Java" in version_spec
        if has_bedrock and has_java:
            msg = "Cannot specify both Java and Bedrock in the version spec."
            raise InvalidVersionError(msg, context={"version_spec": version_spec})
        return "Bedrock" if has_bedrock else "Java"

    @staticmethod
    def _range_bounds(part: str) -> tuple[str, str]:
        bounds = tuple(bound.strip() for bound in part.split("-"))
        if len(bounds) != 2:
            msg = f"Invalid version range format in {part}, expected exactly 2 parts, got {len(bounds)}."
            raise InvalidVersionError(msg, context={"range": part})
        return bounds

    @staticmethod
    def _parse_version_numbers(value: str) -> tuple[int, int, int]:
        numbers = tuple(map(int, value.split(".")))
        if len(numbers) == 2:
            return numbers[0], numbers[1], 0
        if len(numbers) == 3:
            return numbers
        msg = f"Invalid version number: {value!r}."
        raise InvalidVersionError(msg, context={"version": value})

    @classmethod
    def _parse_range_end(
        cls,
        value: str,
        available: list[tuple[int, int, int]],
    ) -> tuple[int, int, int]:
        numbers = tuple(map(int, value.split(".")))
        if len(numbers) == 3:
            return numbers
        if len(numbers) != 2:
            msg = f"Invalid version number: {value!r}."
            raise InvalidVersionError(msg, context={"version": value})
        major, minor = numbers
        patches = [
            patch
            for candidate_major, candidate_minor, patch in available
            if (candidate_major, candidate_minor) == numbers
        ]
        return major, minor, max(patches, default=0)
