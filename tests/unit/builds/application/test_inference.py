"""Build inference application tests."""

from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock

import pytest
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
from squid.submissions.application.forms import build_submission_manifest
from squid.submissions.application.prefill import project_submission
from squid.submissions.domain import SubmissionOrigin
from squid.versions.errors import InvalidVersionError


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
        "opening_time": {"value": "1.0s", "qualifier": None},
        "closing_time": {"value": "0.5s", "qualifier": None},
        "creators": ["BuilderOne", "BuilderTwo"],
        "version_spec": "1.20+",
        "author_notes": [],
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
    async def inference_vocabulary(self) -> str:
        return '{"component": ["No Observers"]}'

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
    def __init__(self) -> None:
        self.resolved_specs: list[str] = []

    async def resolve_spec(self, version_spec: str) -> list[str]:
        self.resolved_specs.append(version_spec)
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
    assert generator.calls[0][0] == 'system prompt\n\nAPPROVED TAXONOMY\n{"component": ["No Observers"]}'
    assert '<message id="10" author="Builder"' in generator.calls[0][1]
    assert generator.calls[0][2:] == (InferenceResult, "test-model", (), "low")
    # Both primary messages are retained in submission order, each keeping its own
    # content: the bundle is a body message plus a follow-up, not one concatenation.
    assert [message.id for message in build.source_messages] == [10, 11]
    assert [message.content for message in build.source_messages] == ["A redstone door", "and it is fast"]
    assert build.source_messages[0].channel_id == 30
    assert build.source_messages[0].guild_id == 40
    assert str(build.source_messages[0].created_at) == "2026-01-01T00:00:00Z"
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
    assert build.versions == ["Java 1.20.0", "Java 1.21.0"]
    assert build.description is None
    assert build.normal_opening_time == 20
    assert build.normal_closing_time == 10


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


@pytest.mark.parametrize("version_spec", [None, "", "   "])
async def test_missing_version_stays_unknown(version_spec: str | None) -> None:
    versions = FakeVersions()
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(version_spec=version_spec)])),
        FakeTaxonomy(),
        versions,
        "prompt",
    )

    builds = await service.infer(source(), model="model")

    assert builds[0].version_spec is None
    assert builds[0].versions == []
    assert versions.resolved_specs == []


@pytest.mark.parametrize("error", [ValueError("invalid number"), InvalidVersionError("invalid selection")])
async def test_invalid_version_preserves_candidates_for_review(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = FakeVersions()
    resolve = AsyncMock(side_effect=[error, ["Java 1.20.0"]])
    monkeypatch.setattr(versions, "resolve_spec", resolve)
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(version_spec="Java unknown"), inferred()])),
        FakeTaxonomy(),
        versions,
        "prompt",
    )

    builds = await service.infer(source(), model="model")

    assert len(builds) == 2
    assert builds[0].version_spec == "Java unknown"
    assert builds[0].versions == []
    assert builds[1].versions == ["Java 1.20.0"]


async def test_qualified_timings_and_volume_survive_submission_prefill() -> None:
    service = BuildInferenceService(
        FakeTextGenerator(
            InferenceResult(
                builds=[
                    inferred(
                        opening_time={"value": "1.05s", "qualifier": "no input delay"},
                        closing_time={"value": "1.35s", "qualifier": "+ 3-gt input delay"},
                        volume="936 blocks",
                        completion_time="Feb 2, 2020",
                        display_name="Smallest 1.05s Seamless 5x5 Piston Door",
                    )
                ]
            )
        ),
        FakeTaxonomy(),
        FakeVersions(),
        "prompt",
    )

    (build,) = await service.infer(source(), model="model")
    prefill = project_submission(build, build_submission_manifest(), {}, origin=SubmissionOrigin.DISCORD)

    assert prefill.answers["opening_time"] == 21
    assert prefill.answers["closing_time"] == 27
    assert prefill.answers["completion"] == "Feb 2, 2020"
    assert prefill.answers["display_name"] == "Smallest 1.05s Seamless 5x5 Piston Door"
    assert "Opening: 1.05s; no input delay" in str(prefill.answers["description"])
    assert "Closing: 1.35s; + 3-gt input delay" in str(prefill.answers["description"])
    assert "Reported volume: 936 blocks" in str(prefill.answers["description"])
    assert "936 blocks" not in str(build.extra_info)


async def test_unparseable_timings_and_ambiguous_claims_remain_reviewable() -> None:
    service = BuildInferenceService(
        FakeTextGenerator(
            InferenceResult(
                builds=[
                    inferred(
                        opening_time={"value": "between 1 and 2 seconds", "qualifier": None},
                        review_notes=["Unclear whether flush + funnel describes one build or two variants."],
                    )
                ]
            )
        ),
        FakeTaxonomy(),
        FakeVersions(),
        "prompt",
    )

    (build,) = await service.infer(source(), model="model")

    assert build.normal_opening_time is None
    assert build.description is not None
    assert "Opening: between 1 and 2 seconds" in build.description
    assert "Needs review: Unclear whether flush + funnel" in build.description


async def test_source_links_survive_prefill_without_inventing_or_inheriting_urls() -> None:
    url = "https://youtu.be/8j1c4iGM3yI"
    reddit = "https://www.reddit.com/r/redstone/comments/example/"
    service = BuildInferenceService(
        FakeTextGenerator(
            InferenceResult(
                builds=[
                    inferred(
                        links=[
                            {"url": url, "media_type": "video"},
                            {"url": url, "media_type": "video"},
                            {"url": reddit, "media_type": None},
                            {"url": "https://example.com/invented", "media_type": "image"},
                        ],
                    )
                ]
            )
        ),
        FakeTaxonomy(),
        FakeVersions(),
        "prompt",
    )
    inputs = BuildInferenceInput.from_single_message(
        author_name="Builder",
        content=f"6x6 door\n{url}\n{reddit}",
        message_id=10,
        author_id=20,
        channel_id=30,
        server_id=40,
    )

    (build,) = await service.infer(inputs, model="model")
    prefill = project_submission(build, build_submission_manifest(), {}, origin=SubmissionOrigin.DISCORD)

    assert [(link.url, link.media_type) for link in build.links] == [(url, "video")]
    assert str(prefill.answers["description"]).count(url) == 1
    assert reddit in str(prefill.answers["description"])
    assert "invented" not in str(prefill.answers["description"])


async def test_utility_does_not_acquire_a_door_orientation() -> None:
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(build_category="Utility", door_orientation=None)])),
        FakeTaxonomy(),
        FakeVersions(),
        "prompt",
    )

    (build,) = await service.infer(source(), model="model")

    assert build.door_orientation is None


async def test_author_notes_reject_comparisons_inferred_from_an_outdated_post() -> None:
    previous = "[OUTDATED] 6x6 trapdoor by Exorden. Dimensions: 12x19x3. Size: 684 blocks. Completed 16 March 2020."
    current = "6x6 trapdoor by Exorden. Dimensions: 12x18x3. Size: 648 blocks. Completed 17 March 2020."
    summary = (
        "Updated from the outdated version: the build height decreased from 19 to 18 blocks, "
        "and its size decreased from 684 to 648 blocks."
    )
    inputs = BuildInferenceInput(
        primary=(ContextMessage(10, "Exorden", 20, current, "", "primary"),),
        context=(ContextMessage(9, "Exorden", 20, previous, "", "preceding"),),
        images=(),
        channel_id=30,
        server_id=40,
    )
    service = BuildInferenceService(
        FakeTextGenerator(
            InferenceResult(
                builds=[
                    inferred(
                        build_width=12,
                        build_height=18,
                        build_depth=3,
                        volume="648 blocks",
                        author_notes=[{"source_message_id": 10, "text": summary}],
                    )
                ]
            )
        ),
        FakeTaxonomy(),
        FakeVersions(),
        "prompt",
    )

    (build,) = await service.infer(inputs, model="model")
    prefill = project_submission(build, build_submission_manifest(), {}, origin=SubmissionOrigin.DISCORD)

    assert build.dimensions == (12, 18, 3)
    assert build.description == "Reported volume: 648 blocks"
    assert prefill.answers["description"] == "Reported volume: 648 blocks"


@pytest.mark.parametrize(
    ("message_id", "text"),
    [
        (9, "Layout by OriginalBuilder."),
        (11, "Fixed input dependency."),
        (999, "Added a reset button."),
        (10, "Fixed input dependency."),
        (10, "The author added a reset button."),
        (10, " \n "),
    ],
)
async def test_author_notes_require_an_excerpt_from_this_builds_selected_sources(message_id: int, text: str) -> None:
    inputs = BuildInferenceInput(
        primary=(
            ContextMessage(10, "Builder", 20, "Added a reset button.", "", "primary"),
            ContextMessage(11, "Builder", 20, "Fixed input dependency.", "", "primary"),
        ),
        context=(ContextMessage(9, "Other", 21, "Layout by OriginalBuilder.", "", "preceding"),),
        images=(),
        channel_id=30,
        server_id=40,
    )
    service = BuildInferenceService(
        FakeTextGenerator(
            InferenceResult(
                builds=[
                    inferred(
                        author_notes=[{"source_message_id": message_id, "text": text}],
                    )
                ]
            )
        ),
        FakeTaxonomy(),
        FakeVersions(),
        "prompt",
    )

    (build,) = await service.infer(inputs, model="model")

    assert build.description is None


async def test_author_notes_preserve_explicit_changes_and_requirements_without_rewriting_them() -> None:
    change = "V2: reduced height from 19 to 18 blocks."
    requirement = "Requires a 2-gt pulse."
    fix = "Fixed input dependency."
    inputs = BuildInferenceInput(
        primary=(
            ContextMessage(10, "Builder", 20, f"{change}\n{requirement}", "", "primary"),
            ContextMessage(11, "Builder", 20, fix, "", "primary"),
        ),
        context=(),
        images=(),
        channel_id=30,
        server_id=40,
    )
    service = BuildInferenceService(
        FakeTextGenerator(
            InferenceResult(
                builds=[
                    inferred(
                        source_message_ids=[10, 11],
                        author_notes=[
                            {"source_message_id": 10, "text": change},
                            {"source_message_id": 10, "text": "Requires a 2-gt\npulse."},
                            {"source_message_id": 11, "text": fix},
                            {"source_message_id": 10, "text": change},
                        ],
                    )
                ]
            )
        ),
        FakeTaxonomy(),
        FakeVersions(),
        "prompt",
    )

    (build,) = await service.infer(inputs, model="model")
    prefill = project_submission(build, build_submission_manifest(), {}, origin=SubmissionOrigin.DISCORD)

    assert build.description == f"{change}\n{requirement}\n{fix}"
    assert prefill.answers["description"] == build.description
