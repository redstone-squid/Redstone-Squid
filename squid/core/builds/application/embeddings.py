"""Application coordination for build embeddings and semantic lookup."""

import json
from typing import Protocol

from squid.core.builds.domain import UnresolvedTaxonomy
from squid.core.builds.models import Build, DoorBuild, ExtenderBuild
from squid.core.errors import InvalidStateError
from squid.core.events import DomainEvent, UnsupportedEventVersionError
from squid.core.unit_of_work import UnitOfWork, UnitOfWorkFactory


class EmbeddingModel(Protocol):
    """Generate an embedding vector for text."""

    @property
    def enabled(self) -> bool: ...

    async def embed(self, text: str) -> list[float] | None: ...


class BuildVectorIndex(Protocol):
    """Persist and query build embedding vectors."""

    async def fill_missing(self, build_id: int, revision: int, embedding: list[float]) -> bool: ...

    async def find_nearest(self, embedding: list[float]) -> int | None: ...


class BuildEmbeddingService:
    """Index committed builds and resolve semantic queries."""

    def __init__(self, model: EmbeddingModel, *, uow_factory: UnitOfWorkFactory[BuildVectorUnitOfWork]) -> None:
        self._model = model
        self._uow_factory = uow_factory

    async def handle(self, event: DomainEvent) -> None:
        """Generate a missing vector and install it only on the revision read."""
        versions = (1, 2) if event.event_type == "build.submitted" else (1,)
        if event.schema_version not in versions:
            raise UnsupportedEventVersionError
        if not self._model.enabled:
            return
        async with self._uow_factory() as uow:
            build = await uow.builds.get_by_id(event.aggregate_id)
            if build is None or build.embedding is not None:
                return
            text = _embedding_text(build)
            revision = build.revision
        embedding = await self._model.embed(text)
        if embedding is None:
            message = "Configured provider returned no build embedding."
            raise InvalidStateError(message)
        async with self._uow_factory() as uow:
            if await uow.build_vectors.fill_missing(event.aggregate_id, revision, embedding):
                await uow.commit()
                return
            current = await uow.builds.get_by_id(event.aggregate_id)
            if current is not None and current.embedding is None:
                message = "Build changed while generating its embedding."
                raise InvalidStateError(message)

    async def find_build_id(self, query: str) -> int | None:
        """Find the build nearest to a natural-language query."""
        embedding = await self._model.embed(query)
        if embedding is None:
            return None
        async with self._uow_factory() as uow:
            return await uow.build_vectors.find_nearest(embedding)


def _embedding_text(build: Build) -> str:
    """Describe staged build facts without traversing ORM relationships or private metadata."""
    unresolved = UnresolvedTaxonomy.read(build.extra_info)
    values: dict[str, object] = {
        "category": build.category.value,
        "name": build.display_name,
        "description": build.description,
        "creators": build.creators_ign,
        "dimensions": build.dimensions,
        "versions": build.versions,
        "version_compatibility": build.version_spec,
        "patterns": [*build.patterns, *unresolved.patterns],
        "restrictions": {
            field: [*(names or ()), *unresolved.restriction_names(field)] for field, names in build.restrictions.items()
        },
    }
    if isinstance(build, DoorBuild):
        values.update(
            opening_dimensions=build.door_dimensions,
            orientation=build.orientation,
            opening_time=build.normal_opening_time,
            closing_time=build.normal_closing_time,
        )
    elif isinstance(build, ExtenderBuild):
        values.update(orientation=build.orientation, extension_length=build.extension_length)
    return json.dumps(values, ensure_ascii=False)


class EmbeddingBuildReader(Protocol):
    async def get_by_id(self, build_id: int) -> Build | None: ...


class BuildVectorUnitOfWork(UnitOfWork, Protocol):
    @property
    def builds(self) -> EmbeddingBuildReader: ...

    @property
    def build_vectors(self) -> BuildVectorIndex: ...
