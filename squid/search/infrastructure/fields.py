"""Database-backed search fields for approved data-tags."""

from collections.abc import Callable
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.search.application.fields import (
    DEFAULT_FIELD_REGISTRY,
    FieldDefinition,
    FieldRegistry,
    FieldType,
)
from squid.tags.models import TagDefinition, UnitDefinition


class PostgresFieldRegistryProvider:
    """Build a current search registry from approved tag definitions."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def registry(self) -> FieldRegistry:
        """Add approved tags with query names and typed values to the built-in fields."""
        session = self._session()
        definitions = tuple(
            (
                await session.scalars(
                    select(TagDefinition).where(
                        TagDefinition.moderation_status == "approved",
                        TagDefinition.query_name.is_not(None),
                        TagDefinition.value_type != "none",
                    )
                )
            ).all()
        )
        units = {unit.key: unit for unit in (await session.scalars(select(UnitDefinition))).all()}
        dynamic = tuple(_field_definition(definition, units) for definition in definitions)
        return FieldRegistry((*DEFAULT_FIELD_REGISTRY.definitions, *dynamic))


def _field_definition(
    definition: TagDefinition,
    units: dict[str, UnitDefinition],
) -> FieldDefinition:
    assert definition.query_name is not None
    value_type = {
        "numeric": FieldType.NUMBER,
        "text": FieldType.TEXT,
        "boolean": FieldType.BOOLEAN,
    }[definition.value_type]
    unit_scales: tuple[tuple[str, Decimal], ...] = ()
    canonical = units.get(definition.canonical_unit or "")
    if canonical is not None:
        aliases: list[tuple[str, Decimal]] = []
        for unit in units.values():
            if unit.dimension != canonical.dimension:
                continue
            # Query values must use the tag's canonical unit, which may differ from the base unit.
            scale = unit.scale_to_base / canonical.scale_to_base
            aliases.extend((alias, scale) for alias in {unit.key, unit.symbol, *unit.aliases})
        unit_scales = tuple(aliases)
    return FieldDefinition(
        name=definition.query_name,
        value_type=value_type,
        supports_range=value_type is FieldType.NUMBER,
        storage_name=f"tag:{definition.id}",
        supports_sort=True,
        unit_scales=unit_scales,
        numeric_step=definition.numeric_step,
    )
