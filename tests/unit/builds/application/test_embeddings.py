"""Build embedding application tests."""

import json
from functools import partial
from typing import override

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import make_transient_to_detached

from squid.core.actors import ActingContext, Actor
from squid.core.builds.application import BuildEmbeddingService
from squid.core.builds.domain.credits import name_credits
from squid.core.builds.models import Build, DoorBuild, OtherBuild
from squid.core.errors import InvalidStateError
from squid.core.events import DomainEvent
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagAssignment, TagDefinition
from tests.support.unit_of_work import FakeBuildEditing, PublishingFakeUnitOfWork


class FakeEmbeddingModel:
    enabled = True

    def __init__(self, embedding: list[float] | None) -> None:
        self.embedding = embedding
        self.inputs: list[str] = []

    async def embed(self, text: str) -> list[float] | None:
        self.inputs.append(text)
        return self.embedding


class FakeVectorIndex(FakeBuildEditing):
    def __init__(self, build: Build) -> None:
        self.build = build

    async def get_by_id(self, build_id: int) -> Build | None:
        return self.build if self.build.id == build_id else None

    async def fill_missing(self, build_id: int, revision: int, embedding: list[float]) -> bool:
        if self.build.revision != revision or self.build.embedding is not None:
            return False
        self.build.embedding = embedding
        return True

    async def find_nearest(self, embedding: list[float]) -> int | None:
        return self.build.id


class VectorScope(PublishingFakeUnitOfWork[FakeVectorIndex]):
    @property
    def build_vectors(self) -> FakeVectorIndex:
        return self.state

    @property
    def builds(self) -> FakeVectorIndex:
        return self.state


def edited() -> DomainEvent:
    return DomainEvent(event_type="build.edited", aggregate_kind="build", aggregate_id=7)


async def test_retry_skips_completed_vector_and_lookup_uses_index() -> None:
    model = FakeEmbeddingModel([1.0, 2.0])
    index = FakeVectorIndex(OtherBuild(id=7))
    service = BuildEmbeddingService(model, uow_factory=partial(VectorScope, index))
    await service.handle(edited(), context=ActingContext.direct(Actor()))
    await service.handle(edited(), context=ActingContext.direct(Actor()))
    assert len(model.inputs) == 1
    assert index.build.embedding == [1.0, 2.0]
    assert await service.find_build_id("compact door", context=ActingContext.direct(Actor())) == 7


async def test_missing_configured_vector_retries_and_disabled_provider_skips() -> None:
    model = FakeEmbeddingModel(None)
    index = FakeVectorIndex(OtherBuild(id=7))
    service = BuildEmbeddingService(model, uow_factory=partial(VectorScope, index))
    with pytest.raises(InvalidStateError):
        await service.handle(edited(), context=ActingContext.direct(Actor()))
    model.enabled = False
    await service.handle(edited(), context=ActingContext.direct(Actor()))
    assert len(model.inputs) == 1
    assert index.build.embedding is None


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
        id=7,
        door_width=6,
        door_height=6,
        orientation="Door",
        patterns=["Regular"],
        tags=[TagAssignment(definition)],
        credits=name_credits(["Builder"], legacy=True),
        description="A compact door",
        extra_info={"unknown_restrictions": {"component_restrictions": ["Sandless"]}},
    )
    model = FakeEmbeddingModel([1.0, 2.0])
    index = FakeVectorIndex(build)
    service = BuildEmbeddingService(model, uow_factory=partial(VectorScope, index, clone=lambda value: value))

    await service.handle(edited(), context=ActingContext.direct(Actor()))

    text = json.loads(model.inputs[0])
    assert text["category"] == "Door"
    assert text["patterns"] == ["Regular"]
    assert text["creators"] == ["Builder"]
    assert text["description"] == "A compact door"
    assert text["opening_dimensions"] == [6, 6, None]
    assert text["restrictions"]["component_restrictions"] == ["Sandless"]
    assert build.embedding == [1.0, 2.0]


async def test_moderation_during_generation_retries_against_the_new_revision() -> None:
    index = FakeVectorIndex(OtherBuild(id=7, revision=1))

    class RacingModel(FakeEmbeddingModel):
        @override
        async def embed(self, text: str) -> list[float] | None:
            if not self.inputs:
                index.build.revision += 1
            return await super().embed(text)

    model = RacingModel([1.0, 2.0])
    service = BuildEmbeddingService(model, uow_factory=partial(VectorScope, index))
    with pytest.raises(InvalidStateError, match="Build changed"):
        await service.handle(edited(), context=ActingContext.direct(Actor()))

    assert index.build.embedding is None
    await service.handle(edited(), context=ActingContext.direct(Actor()))
    assert index.build.embedding == [1.0, 2.0]
