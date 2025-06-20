from dataclasses import dataclass
from typing import Any, Literal


@dataclass(slots=True)
class Version:
    """A version of Minecraft that a build is compatible with."""

    edition: Literal["Java", "Bedrock"]
    major_version: int
    minor_version: int
    patch_number: int

    def __str__(self) -> str:
        """String representation for display purposes."""
        return f"{self.edition} {self.major_version}.{self.minor_version}.{self.patch_number}"


class VersionRepository:
    """Repository for Version persistence and queries."""

    async def fetch_all(self) -> list[Any]: ...
    async def get_versions_list(self, edition: Literal["Java", "Bedrock"]) -> list[Any]: ...
    async def get_newest_version(self, edition: Literal["Java", "Bedrock"]) -> str: ...
    async def find_versions_from_spec(self, version_spec: str) -> list[str]: ...


class VersionService:
    """Service for Version domain logic and orchestration."""

    async def list_versions(self, edition: Literal["Java", "Bedrock"]) -> list[Any]: ...
    async def get_latest_version(self, edition: Literal["Java", "Bedrock"]) -> str: ...
    async def resolve_versions(self, version_spec: str) -> list[str]: ...
