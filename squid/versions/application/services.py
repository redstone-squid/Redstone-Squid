"""Minecraft version application services."""

from squid.versions.application.ports import VersionRepository
from squid.versions.domain import Edition, MinecraftVersion, parse_version_string
from squid.versions.errors import InvalidVersionError, VersionCatalogUnavailableError


class VersionService:
    """Parse, store, and list recognized Minecraft versions."""

    def __init__(self, repository: VersionRepository):
        self._repository = repository

    async def add(self, version_string: str, *, edition: Edition | None = None) -> MinecraftVersion:
        parsed_edition, major, minor, patch = parse_version_string(version_string)
        version = MinecraftVersion(edition or parsed_edition, major, minor, patch)
        return await self._repository.add(version)

    async def list_versions(self, edition: Edition) -> list[MinecraftVersion]:
        return await self._repository.list(edition)

    async def list_display(self, edition: Edition, *, limit: int = 20) -> list[str]:
        return [str(version) for version in (await self.list_versions(edition))[:limit]]

    async def newest(self, edition: Edition) -> str:
        """Return the newest recognized version for an edition."""
        versions = await self.list_versions(edition)
        if not versions:
            raise VersionCatalogUnavailableError(edition)
        return str(versions[-1])

    async def resolve_spec(self, version_spec: str) -> list[str]:
        """Resolve a version range specification against recognized versions."""
        edition = self._edition_from_spec(version_spec)
        normalized_spec = version_spec.replace("Java", "").replace("Bedrock", "").strip()
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
