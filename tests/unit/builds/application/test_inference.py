"""Build inference application tests."""

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from squid.builds.application import (
    BuildInferenceInput,
    BuildInferenceService,
    ContextMessage,
    InferenceResult,
    InferredBuild,
    InlineImage,
)
from squid.builds.domain import RestrictionTypeLiteral
from squid.versions.domain import Edition


def inferred(**overrides: Any) -> InferredBuild:
    values: dict[str, Any] = {
        "source_message_ids": [10],
        "build_category": "Piston Door",
        "component_restrictions": ["No Observers", "Mystery"],
        "wiring_placement_restrictions": ["Seamless"],
        "animated_restrictions": ["Symmetrical"],
        "miscellaneous_restrictions": [],
        "door_type": ["Regular", "Unknown Pattern"],
        "door_orientation": "Normal",
        "door_width": 3,
        "door_height": 4,
        "door_depth": 1,
        "build_width": 8,
        "build_height": 9,
        "build_depth": 10,
        "opening_time": "1.0s",
        "closing_time": "0.5s",
        "creators": ["BuilderOne", "BuilderTwo"],
        "version_spec": "1.20+",
        "author_note": "first line\nsecond line",
        "confidence": "high",
    }
    values.update(overrides)
    return InferredBuild.model_validate(values)


class FakeTextGenerator:
    def __init__(self, output: InferenceResult | None) -> None:
        self.output = output
        self.calls: list[tuple[str, str, type[BaseModel], str, Sequence[InlineImage], str | None]] = []

    async def generate[T: BaseModel](
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        model: str,
        images: Sequence[InlineImage] = (),
        reasoning_effort: str | None = None,
    ) -> T | None:
        self.calls.append((system, user, schema, model, images, reasoning_effort))
        return self.output  # type: ignore[return-value]


class FakeTaxonomy:
    async def validate_restrictions(
        self,
        restrictions: list[str],
        type: RestrictionTypeLiteral,
    ) -> tuple[list[str], list[str]]:
        return [value for value in restrictions if value != "Mystery"], [
            value for value in restrictions if value == "Mystery"
        ]

    async def validate_door_types(self, door_types: list[str]) -> tuple[list[str], list[str]]:
        return [value for value in door_types if value == "Regular"], [
            value for value in door_types if value != "Regular"
        ]


class FakeVersions:
    async def newest(self, edition: Edition) -> str:
        return f"{edition} 1.21.0"

    async def resolve_spec(self, version_spec: str) -> list[str]:
        return ["Java 1.20.0", "Java 1.21.0"]


def source(*, images: tuple[InlineImage, ...] = ()) -> BuildInferenceInput:
    return BuildInferenceInput(
        primary=(
            ContextMessage(10, "Builder", 20, "A redstone door", "2026-01-01T00:00:00Z", "primary", "1 image"),
            ContextMessage(11, "Builder", 20, "and it is fast", "2026-01-01T00:00:05Z", "primary"),
        ),
        context=(ContextMessage(9, "Other", 21, "Earlier context", "2025-12-31T23:59:00Z", "preceding"),),
        images=images,
        channel_id=30,
        server_id=40,
    )


async def test_infer_maps_structured_build_and_validates_taxonomy() -> None:
    generator = FakeTextGenerator(InferenceResult(builds=[inferred(source_message_ids=[11, 10])]))
    service = BuildInferenceService(generator, FakeTaxonomy(), FakeVersions(), "system prompt")

    builds = await service.infer(source(), model="test-model", reasoning_effort="low")

    assert len(builds) == 1
    build = builds[0]
    assert generator.calls[0][0] == "system prompt"
    assert '<message id="10" author="Builder"' in generator.calls[0][1]
    assert generator.calls[0][2:] == (InferenceResult, "test-model", (), "low")
    assert build.original_message is not None
    assert build.original_message.message_id == 10
    assert build.original_message.content == "A redstone door\nand it is fast"
    assert build.category is not None
    assert build.category.value == "Door"
    assert build.door_dimensions == (3, 4, 1)
    assert build.dimensions == (8, 9, 10)
    assert build.component_restrictions == ["No Observers"]
    unknown_restrictions = build.extra_info.get("unknown_restrictions")
    assert unknown_restrictions is not None
    assert unknown_restrictions.get("component_restrictions") == ["Mystery"]
    assert build.patterns == ["Regular"]
    assert build.extra_info.get("unknown_patterns") == ["Unknown Pattern"]
    assert build.version_spec == "1.20+"
    assert build.extra_info.get("user") == "first line\nsecond line"


async def test_infer_returns_multiple_or_no_builds() -> None:
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(), inferred(source_message_ids=[11])])),
        FakeTaxonomy(),
        FakeVersions(),
        "prompt",
    )
    assert len(await service.infer(source(), model="model")) == 2

    empty_service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[])), FakeTaxonomy(), FakeVersions(), "prompt"
    )
    assert await empty_service.infer(source(), model="model") == []


async def test_infer_discards_context_and_bogus_source_ids() -> None:
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(source_message_ids=[9, 999])])),
        FakeTaxonomy(),
        FakeVersions(),
        "prompt",
    )
    assert await service.infer(source(), model="model") == []


async def test_infer_passes_images_to_generator() -> None:
    image = InlineImage(b"image", "image/png", 10, "attachment")
    generator = FakeTextGenerator(InferenceResult(builds=[]))
    service = BuildInferenceService(generator, FakeTaxonomy(), FakeVersions(), "prompt")

    await service.infer(source(images=(image,)), model="model")

    assert generator.calls[0][4] == (image,)
