"""Resolve schematic data versions through the shared version catalogue."""

from typing import Protocol

from squid.schematics.application.ports import SchematicVersionResolver
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


class SchematicVersionService:
    def __init__(self, *, uow_factory: UnitOfWorkFactory[SchematicVersionUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def data_version_for(self, version_label: str) -> int | None:
        async with self._uow_factory() as uow:
            return await uow.schematic_versions.data_version_for(version_label)

    async def label_for_data_version(self, data_version: int) -> str | None:
        async with self._uow_factory() as uow:
            return await uow.schematic_versions.label_for_data_version(data_version)


class SchematicVersionUnitOfWork(UnitOfWork, Protocol):
    @property
    def schematic_versions(self) -> SchematicVersionResolver: ...
