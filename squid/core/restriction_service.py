from dataclasses import dataclass, field

from squid.db.schema import BuildCategory, RestrictionStr


@dataclass(slots=True)
class Restriction:
    """A restriction that can be applied to builds."""

    id: int | None = None
    name: str
    build_category: BuildCategory | None = None
    type: RestrictionStr | None = None

    aliases: list[str] = field(default_factory=list)

class RestrictionRepository:
    """Repository for Restriction persistence and queries."""

    async def get_restriction_id(self, name_or_alias: str) -> int | None: ...
    async def add_restriction_alias_by_id(self, restriction_id: int, alias: str) -> None: ...
    async def add_restriction_alias(self, name_or_alias: str, alias: str) -> None: ...


class RestrictionService:
    """Service for Restriction domain logic and orchestration."""

    async def find_restriction_id(self, name_or_alias: str) -> int | None: ...
    async def add_alias_to_restriction(self, restriction_id: int, alias: str) -> None: ...
    async def add_alias_by_name(self, name_or_alias: str, alias: str) -> None: ...
