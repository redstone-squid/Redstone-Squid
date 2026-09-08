"""Build restriction application services."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.builds.domain import RestrictionTypeLiteral


@dataclass(frozen=True, slots=True)
class RestrictionDefinition:
    """Restriction fields needed when classifying submission input."""

    name: str
    type: RestrictionTypeLiteral | None


class RestrictionRepository(Protocol):
    """Restriction metadata needed by build submission."""

    async def fetch_all_restrictions(self) -> Sequence[RestrictionDefinition]:
        """Every approved official restriction, in display order."""
        ...

    async def add_alias(self, restriction: str, alias: str) -> None:
        """Attach an alternate spelling to a restriction, matching both names case-insensitively.

        Raises:
            RestrictionNotFoundError: If no restriction goes by `restriction`.
            AliasAlreadyAddedError: If `alias` already resolves to that same restriction.
            AliasInUseError: If `alias` already resolves to a different restriction.
        """
        ...


class RestrictionService:
    """Application operations for restriction names and aliases."""

    def __init__(self, repository: RestrictionRepository):
        self._repository = repository

    async def add_alias(self, restriction: str, alias: str) -> None:
        """Attach an alternate spelling to a restriction.

        Raises:
            RestrictionNotFoundError: If no restriction goes by `restriction`.
            AliasAlreadyAddedError: If `alias` already resolves to that same restriction.
            AliasInUseError: If `alias` already resolves to a different restriction.
        """
        await self._repository.add_alias(restriction, alias)

    async def names(self) -> list[str]:
        """The display names of every approved official restriction, in display order."""
        return [restriction.name for restriction in await self._repository.fetch_all_restrictions()]
