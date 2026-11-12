"""Build embedding application tests."""

import json
from functools import partial

from sqlalchemy import inspect
from sqlalchemy.orm import make_transient_to_detached

from squid.builds.application import BuildEmbeddingService
from squid.builds.models import DoorBuild, OtherBuild
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAssignment, TagDefinition
from tests.support.unit_of_work import PublishingFakeUnitOfWork


class FakeEmbeddingModel:
    def __init__(self, embedding: list[float] | None) -> None:
        self.embedding = embedding
        self.inputs: list[str] = []

    async def embed(self, text: str) -> list[float] | None:
        self.inputs.append(text)
        return self.embedding


class FakeVectorIndex:
    def __init__(self) -> None:
        self.upserts: list[tuple[int, list[float]]] = []
        self.nearest: int | None = 42

    async def upsert(self, build_id: int, embedding: list[float]) -> None:
        self.upserts.append((build_id, embedding))

    async def find_nearest(self, embedding: list[float]) -> int | None:
        return self.nearest


async def test_prepare_index_and_search_share_embedding_abstractions() -> None:
    model = FakeEmbeddingModel([1.0, 2.0])
    index = FakeVectorIndex()
    service = BuildEmbeddingService(model, uow_factory=partial(VectorScope, index))
    build = OtherBuild(id=7)

    await service.prepare(build)
    await service.index(build)
    result = await service.find_build_id("compact door")

    assert build.embedding == [1.0, 2.0]
    assert index.upserts == [(7, [1.0, 2.0])]
    assert result == 42
    assert model.inputs[-1] == "compact door"


async def test_missing_embedding_skips_index_and_search() -> None:
    index = FakeVectorIndex()
    service = BuildEmbeddingService(FakeEmbeddingModel(None), uow_factory=partial(VectorScope, index))
    build = OtherBuild(id=7)

    await service.prepare(build)
    await service.index(build)

    assert await service.find_build_id("query") is None
    assert index.upserts == []


class VectorScope(PublishingFakeUnitOfWork[FakeVectorIndex]):
    @property
    def build_vectors(self) -> FakeVectorIndex:
        return self.state


async def test_prepare_embeds_build_facts_without_rendering_unloaded_tag_relationships() -> None:
    definition = TagDefinition(
        id=1,
        stable_key="regular",
        display_name="Regular",
        authority=TagAuthority.OFFICIAL,
        semantic_kind=TagSemanticKind.PATTERN,
        value_type=TagValueType.NONE,
        moderation_status=TagModerationStatus.APPROVED,
    )
    make_transient_to_detached(definition)
    assert "canonical_unit_definition" in inspect(definition).unloaded
    build = DoorBuild(
        door_width=6,
        door_height=6,
        orientation="Door",
        patterns=["Regular"],
        tags=[TagAssignment(definition)],
        creators_ign=["Builder"],
        description="A compact door",
        extra_info={"unknown_restrictions": {"component_restrictions": ["Sandless"]}},
    )
    model = FakeEmbeddingModel([1.0, 2.0])
    service = BuildEmbeddingService(model, uow_factory=partial(VectorScope, FakeVectorIndex()))

    await service.prepare(build)

    text = json.loads(model.inputs[0])
    assert text["category"] == "Door"
    assert text["patterns"] == ["Regular"]
    assert text["creators"] == ["Builder"]
    assert text["description"] == "A compact door"
    assert text["opening_dimensions"] == [6, 6, None]
    assert text["restrictions"]["component_restrictions"] == ["Sandless"]
    assert build.embedding == [1.0, 2.0]
