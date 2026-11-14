"""Build inference application tests."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, TypeAdapter

from squid.builds.application import (
    BuildInferenceInput,
    BuildInferenceService,
    ContextIdentity,
    ContextMessage,
    InferenceResult,
    InferredBuild,
    InlineImage,
)
from squid.builds.domain import RestrictionTypeLiteral
from squid.builds.domain.credits import Credit, CreditAttribution, CreditKind, CreditRole
from squid.submissions.application.forms import build_submission_manifest
from squid.submissions.application.prefill import project_submission
from squid.submissions.domain import SubmissionOrigin
from squid.submissions.infrastructure.inference_payloads import BuildDraftPayload
from squid.versions.errors import InvalidVersionError
from tests.support.expansion import expansion_document


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
        "visible_opening_time": None,
        "visible_closing_time": None,
        "credits": [
            {"name": name, "kind": "name", "role": "creator", "source_message_id": 10}
            for name in ["BuilderOne", "BuilderTwo"]
        ],
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
        (10, "Added a &#x27;new&#x27; reset button."),
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


@pytest.mark.parametrize(
    ("content", "excerpt", "expected"),
    [
        ("it's easy to modify for 1.11+", "it&#x27;s easy to modify for 1.11+", "it's easy to modify for 1.11+"),
        ("it's easy to modify for 1.11+", "it\u2019s easy to modify for 1.11+", "it's easy to modify for 1.11+"),
        ("it\u2019s easy to modify for 1.11+", "it's easy to modify for 1.11+", "it\u2019s easy to modify for 1.11+"),
        ("it's easy to modify for 1.11+", "it&#x2019;s easy to modify for 1.11+", "it's easy to modify for 1.11+"),
        ("Requires A & B.", "Requires A &amp; B.", "Requires A & B."),
        ("Keep 'A' & \"B\".", "Keep &#x2018;A&#x2019; &amp; &#x201c;B&#x201d;.", "Keep 'A' & \"B\"."),
        ("Keep &#x27; literal.", "Keep &#x27; literal.", "Keep &#x27; literal."),
        ("Keep &#x27; literal.", "Keep &amp;#x27; literal.", "Keep &#x27; literal."),
        ("Keep &#x27; literal.", "Keep ' literal.", None),
        ("Keep ' literal.", "Keep &amp;#x27; literal.", None),
    ],
)
async def test_author_notes_recover_transport_escaping_without_rewriting_source(
    content: str, excerpt: str, expected: str | None
) -> None:
    inputs = BuildInferenceInput.from_single_message(
        author_name="Builder", content=content, message_id=10, author_id=20, channel_id=30, server_id=40
    )
    service = BuildInferenceService(
        FakeTextGenerator(
            InferenceResult(builds=[inferred(author_notes=[{"source_message_id": 10, "text": excerpt}])])
        ),
        FakeTaxonomy(),
        FakeVersions(),
        "prompt",
    )

    (build,) = await service.infer(inputs, model="model")

    assert build.description == expected


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


def test_missing_creator_uses_a_marked_discord_sender_credit() -> None:
    credits = BuildInferenceService._credits(inferred(credits=[]), source().primary)
    assert credits == [
        Credit(
            name="Builder",
            kind=CreditKind.DISCORD,
            subject="20",
            attribution=CreditAttribution.ASSUMED_SENDER,
            source_message_id=10,
        )
    ]


def test_explicit_names_and_contributions_do_not_become_minecraft_usernames() -> None:
    value = inferred(
        credits=[
            {"name": "Alice", "kind": "name", "role": "creator", "source_message_id": 10},
            {"name": "Bob", "kind": "name", "role": "layout", "source_message_id": 10},
        ]
    )
    credits = BuildInferenceService._credits(value, source().primary)
    assert [(credit.name, credit.kind, credit.role) for credit in credits] == [
        ("Alice", CreditKind.NAME, CreditRole.CREATOR),
        ("Bob", CreditKind.NAME, CreditRole.LAYOUT),
    ]
    assert all(credit.subject is None and credit.username is None for credit in credits)


def test_only_observed_discord_ids_can_resolve_to_an_account() -> None:
    message = ContextMessage(
        10,
        "Poster",
        20,
        "By <@30> with help from Else",
        "",
        "primary",
        mentions=(ContextIdentity(30, "Alice", "alice_d"),),
    )
    value = inferred(
        credits=[
            {"name": "wrong snapshot", "kind": "discord", "subject": "30", "role": "creator", "source_message_id": 10},
            {"name": "Else", "kind": "discord", "subject": "999", "role": "contributor", "source_message_id": 10},
        ]
    )
    credits = BuildInferenceService._credits(value, [message])
    assert (credits[0].name, credits[0].username, credits[0].subject) == ("Alice", "alice_d", "30")
    assert credits[1].subject is None


def test_contributor_only_keeps_the_sender_assumption_and_other_build_sources_are_ignored() -> None:
    value = inferred(
        credits=[
            {"name": "Bob", "kind": "name", "role": "contributor", "source_message_id": 10},
            {"name": "Other build", "kind": "name", "role": "creator", "source_message_id": 999},
        ]
    )
    credits = BuildInferenceService._credits(value, source().primary)
    assert [credit.name for credit in credits] == ["Builder", "Bob"]
    assert credits[0].attribution is CreditAttribution.ASSUMED_SENDER


async def test_visible_and_true_timings_are_kept_separately_through_prefill() -> None:
    value = inferred(
        opening_time={"value": "1.35s", "qualifier": None},
        closing_time={"value": "1.35s", "qualifier": None},
        visible_opening_time={"value": "1.25s", "qualifier": None},
        visible_closing_time={"value": "1.25s", "qualifier": None},
    )
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[value])), FakeTaxonomy(), FakeVersions(), "system"
    )
    [build] = await service.infer(source(), model="test")
    assert (build.normal_opening_time, build.normal_closing_time) == (27, 27)
    assert (build.visible_opening_time, build.visible_closing_time) == (25, 25)
    projected = project_submission(build, build_submission_manifest(), {}, origin=SubmissionOrigin.DISCORD)
    assert [
        projected.answers[field]
        for field in ("opening_time", "closing_time", "visible_opening_time", "visible_closing_time")
    ] == [27, 27, 25, 25]


async def test_approximate_minutes_keep_both_ticks_and_the_qualification() -> None:
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(opening_time={"value": "~42 minutes", "qualifier": None})])),
        FakeTaxonomy(),
        FakeVersions(),
        "system",
    )
    [build] = await service.infer(source(), model="test")
    assert build.normal_opening_time == 50400
    assert build.description is not None
    assert "~42 minutes" in build.description


async def test_measurement_qualifications_and_variant_ranges_remain_source_excerpts() -> None:
    message = ContextMessage(
        10, "Builder", 20, "Volume: 4992 blocks (including floor)\nbreaks record at: n=5-14 wide", "", "primary"
    )
    value = inferred(
        author_notes=[
            {"source_message_id": 10, "text": "(including floor)"},
            {"source_message_id": 10, "text": "breaks record at: n=5-14 wide"},
            {"source_message_id": 10, "text": "The build counts its floor and breaks several records."},
        ]
    )
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[value])), FakeTaxonomy(), FakeVersions(), "system"
    )
    [build] = await service.infer(BuildInferenceInput((message,), (), (), 30, 40), model="test")
    assert build.description == "(including floor)\nbreaks record at: n=5-14 wide"


async def test_expandable_source_facts_survive_inference_without_sample_dimensions() -> None:
    document = expansion_document()
    text = "\n".join(
        ["A redstone door", document.raw_description or ""]
        + [
            value
            for branch in document.branches
            for value in (
                branch.opening_width,
                branch.opening_height,
                branch.opening_depth,
                branch.volume,
                branch.domain,
                branch.claimed_record_domain,
            )
            if value is not None
        ]
        + [timing.expression for branch in document.branches for timing in branch.timings]
    )
    inputs = source()
    inputs = replace(inputs, primary=(replace(inputs.primary[0], content=text),))
    generator = FakeTextGenerator(
        InferenceResult(
            builds=[
                inferred(
                    door_expansion=document,
                    door_width=None,
                    door_height=None,
                    door_depth=None,
                )
            ]
        )
    )
    service = BuildInferenceService(generator, FakeTaxonomy(), FakeVersions(), "system prompt")
    builds = await service.infer(inputs, model="test-model")
    actual = builds[0].door_expansion
    assert actual is not None
    assert actual.parameters == document.parameters
    assert actual.raw_description == document.raw_description
    assert [branch.model_dump(exclude={"evidence"}) for branch in actual.branches] == [
        branch.model_dump(exclude={"evidence"}) for branch in document.branches
    ]
    assert builds[0].door_dimensions == (None, None, None)


@pytest.mark.parametrize(
    ("kind", "subject", "username"),
    [
        ("name", "20", "builder"),
        ("minecraft_java", "not-a-uuid", "builder"),
        ("minecraft_bedrock", "not-an-xuid", "builder"),
    ],
)
async def test_malformed_provider_fields_do_not_discard_a_build_or_its_other_credits(
    kind: str, subject: str, username: str
) -> None:
    message = ContextMessage(10, "Poster", 20, f"By Builder and Alice; reference {subject}", "", "primary")
    value = inferred(
        credits=[
            {
                "name": "Builder",
                "kind": kind,
                "role": "creator",
                "source_message_id": 10,
                "subject": subject,
                "username": username,
            },
            {"name": "Alice", "kind": "name", "role": "contributor", "source_message_id": 10},
        ]
    )
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[value, inferred(source_message_ids=[11])])),
        FakeTaxonomy(),
        FakeVersions(),
        "system",
    )
    inputs = BuildInferenceInput((message, source().primary[1]), (), (), 30, 40)
    builds = await service.infer(inputs, model="test")

    assert len(builds) == 2
    assert [credit.name for credit in builds[0].credits] == ["Builder", "Alice"]
    assert builds[0].credits[0].subject is None
    assert builds[0].credits[0].username == (None if kind == "name" else username)


@pytest.mark.parametrize("name", ["", " ", "x" * 256])
def test_invalid_credit_names_are_isolated_and_keep_the_sender_fallback(name: str) -> None:
    value = inferred(credits=[{"name": name, "kind": "name", "role": "creator", "source_message_id": 10}])
    credits = BuildInferenceService._credits(value, source().primary)
    assert len(credits) == 1
    assert credits[0].attribution is CreditAttribution.ASSUMED_SENDER


async def test_extender_stroke_and_timings_survive_retained_candidates_and_prefill() -> None:
    value = inferred(
        build_category="Piston Extender",
        door_type=[],
        door_orientation=None,
        door_width=None,
        door_height=None,
        door_depth=None,
        opening_time=None,
        closing_time=None,
        extender_orientation="Upward",
        extension_length=2,
        extension_time={"value": "0s", "qualifier": None},
        retraction_time={"value": "0.25s", "qualifier": "6gt clockable"},
    )
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[value])), FakeTaxonomy(), FakeVersions(), "system"
    )
    [build] = await service.infer(source(), model="test")
    retained = (
        TypeAdapter(BuildDraftPayload)
        .validate_json(TypeAdapter(BuildDraftPayload).dump_json(BuildDraftPayload.from_draft(build)))
        .to_draft()
    )
    projected = project_submission(retained, build_submission_manifest(), {}, origin=SubmissionOrigin.DISCORD)
    assert projected.answers["movement_orientation"] == "vertical_up"
    assert projected.answers["extension_length"] == 2
    assert projected.answers["extension_time"] == 0
    assert projected.answers["retraction_time"] == 5
    description = projected.answers["description"]
    assert isinstance(description, str)
    assert "Retraction: 0.25s; 6gt clockable" in description


async def test_seamless_and_reset_measurements_remain_separate_from_the_duration() -> None:
    value = inferred(opening_time={"value": "0.1s", "qualifier": "0.25s seamless, 0.05s reset"})
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[value])), FakeTaxonomy(), FakeVersions(), "system"
    )
    [build] = await service.infer(source(), model="test")
    assert build.normal_opening_time == 2
    assert build.visible_opening_time is None
    assert build.description == "Opening: 0.1s; 0.25s seamless, 0.05s reset"


async def test_inference_keeps_valid_links_when_the_model_invents_an_empty_or_malformed_url() -> None:
    urls = ["", "javascript:alert(1)", "https://[invalid", "https://example.test/a b", "https://example.test/video.mp4"]
    message = ContextMessage(10, "Builder", 20, "References: " + " ".join(urls), "", "primary")
    value = inferred(links=[{"url": url, "media_type": "video"} for url in urls])
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[value])), FakeTaxonomy(), FakeVersions(), "system"
    )
    [build] = await service.infer(BuildInferenceInput((message,), (), (), 30, 40), model="test")
    assert [link.url for link in build.links] == [urls[-1]]
    assert build.description == "Reference: https://example.test/video.mp4"
    prefill = project_submission(build, build_submission_manifest(), {}, origin=SubmissionOrigin.DISCORD)
    assert prefill.unresolved == ()
    assert prefill.answers["video_urls"] == [urls[-1]]


@pytest.mark.parametrize(("spec", "expected"), [(None, "Bedrock"), ("1.16+", "Bedrock 1.16+")])
async def test_explicit_edition_survives_without_guessing_a_release(spec: str | None, expected: str) -> None:
    versions = FakeVersions()
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(edition="Bedrock", version_spec=spec)])),
        FakeTaxonomy(),
        versions,
        "system",
    )
    [build] = await service.infer(source(), model="test")
    assert build.version_spec == expected
    assert versions.resolved_specs == [expected]


def test_discord_credit_preserves_a_source_alias_with_the_observed_username_and_id() -> None:
    message = ContextMessage(
        10,
        "New Display",
        20,
        "By FormerName (<@30>)",
        "",
        "primary",
        mentions=(ContextIdentity(30, "Current Display", "current_username"),),
    )
    value = inferred(
        credits=[
            {
                "name": "FormerName",
                "kind": "discord",
                "subject": "30",
                "role": "creator",
                "source_message_id": 10,
            }
        ]
    )
    [credit] = BuildInferenceService._credits(value, [message])
    assert credit.name == "FormerName"
    assert credit.username == "current_username"
    assert credit.subject == "30"


async def test_unassigned_timing_pair_is_quoted_without_guessing_its_direction() -> None:
    message = ContextMessage(10, "Builder", 20, "Double piston extender\ninstant/0.25, 6gt clockable", "", "primary")
    value = inferred(
        build_category="Piston Extender",
        opening_time=None,
        closing_time=None,
        extension_length=2,
        extension_time={"value": "instant", "qualifier": None},
    )
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[value])), FakeTaxonomy(), FakeVersions(), "system"
    )
    [build] = await service.infer(BuildInferenceInput((message,), (), (), 30, 40), model="test")
    assert build.extension_time == 0
    assert build.retraction_time is None
    assert build.description == "instant/0.25"


async def test_timing_fallback_does_not_assign_shared_measurements_to_each_variant() -> None:
    message = ContextMessage(10, "Builder", 20, "Two variants: opening .25/.4 and opening .5/.6", "", "primary")
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(opening_time=None), inferred(opening_time=None)])),
        FakeTaxonomy(),
        FakeVersions(),
        "system",
    )
    builds = await service.infer(BuildInferenceInput((message,), (), (), 30, 40), model="test")
    assert [build.description for build in builds] == [None, None]


async def test_timing_fallback_ignores_dates_and_durations_already_represented() -> None:
    message = ContextMessage(10, "Builder", 20, "Completed 12/26/2020\nSpeed: 0.25/0.4s", "", "primary")
    service = BuildInferenceService(
        FakeTextGenerator(
            InferenceResult(
                builds=[
                    inferred(
                        opening_time={"value": "0.25s", "qualifier": None},
                        closing_time={"value": "0.4s", "qualifier": None},
                    )
                ]
            )
        ),
        FakeTaxonomy(),
        FakeVersions(),
        "system",
    )
    [build] = await service.infer(BuildInferenceInput((message,), (), (), 30, 40), model="test")
    assert build.description is None


async def test_a_source_quote_already_covering_an_unparseable_timing_is_not_repeated() -> None:
    excerpt = "Opening: 18:8 seconds + 4.6 second cooldown"
    message = ContextMessage(10, "Builder", 20, excerpt, "", "primary")
    value = inferred(
        opening_time={"value": "18:8 seconds", "qualifier": "+ 4.6 second cooldown"},
        author_notes=[{"source_message_id": 10, "text": excerpt}],
    )
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[value])), FakeTaxonomy(), FakeVersions(), "system"
    )
    [build] = await service.infer(BuildInferenceInput((message,), (), (), 30, 40), model="test")
    assert build.normal_opening_time is None
    assert build.description == excerpt


def test_self_attribution_uses_the_observed_author_name_instead_of_the_word_me() -> None:
    message = ContextMessage(10, "Builder", 20, "Made by me", "", "primary", author_username="builder_d")
    value = inferred(
        credits=[
            {
                "name": "me",
                "kind": "discord",
                "subject": "20",
                "role": "creator",
                "source_message_id": 10,
            }
        ]
    )
    [credit] = BuildInferenceService._credits(value, [message])
    assert credit.name == "Builder"
    assert credit.username == "builder_d"
    assert credit.subject == "20"


async def test_equal_qualified_opening_and_closing_times_keep_both_direction_labels() -> None:
    timing = {"value": "0.45s", "qualifier": "0 gt input delay, 0 gt reset"}
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(opening_time=timing, closing_time=timing)])),
        FakeTaxonomy(),
        FakeVersions(),
        "system",
    )
    [build] = await service.infer(source(), model="test")
    assert build.description == (
        "Opening: 0.45s; 0 gt input delay, 0 gt reset\nClosing: 0.45s; 0 gt input delay, 0 gt reset"
    )


async def test_equal_pair_values_are_not_both_covered_by_one_assigned_measurement() -> None:
    message = ContextMessage(10, "Builder", 20, "Speed: 0.25/0.25s", "", "primary")
    service = BuildInferenceService(
        FakeTextGenerator(
            InferenceResult(
                builds=[
                    inferred(
                        opening_time={"value": "0.25s", "qualifier": None},
                        closing_time=None,
                    )
                ]
            )
        ),
        FakeTaxonomy(),
        FakeVersions(),
        "system",
    )
    [build] = await service.infer(BuildInferenceInput((message,), (), (), 30, 40), model="test")
    assert build.normal_opening_time == 5
    assert build.normal_closing_time is None
    assert build.description == "0.25/0.25s"


async def test_incomplete_expansion_bounds_do_not_discard_a_message_group() -> None:
    value = inferred(
        door_expansion={
            "parameters": [{"name": "x", "maximum_kind": "finite", "maximum": None}],
            "branches": [{"volume": "40+20x blocks", "domain": "x=1-14 wide"}],
        }
    )
    message = ContextMessage(10, "Builder", 20, "40+20x blocks, x=1-14 wide", "", "primary")
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[value, inferred()])),
        FakeTaxonomy(),
        FakeVersions(),
        "system",
    )
    builds = await service.infer(BuildInferenceInput((message,), (), (), 30, 40), model="test")
    assert len(builds) == 2
    assert builds[0].door_expansion is not None
    assert builds[0].door_expansion.parameters[0].maximum_kind == "unknown"
    assert builds[0].door_expansion.branches[0].domain == "x=1-14 wide"


async def test_inference_keeps_explicit_version_exclusions() -> None:
    versions = FakeVersions()
    versions.resolve_spec = AsyncMock(return_value=["Java 1.8.0", "Java 1.15.2"])
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(version_spec="1.8+, !1.16+")])),
        FakeTaxonomy(),
        versions,
        "system prompt",
    )

    (build,) = await service.infer(source(), model="test")

    assert build.version_spec == "1.8+, !1.16+"
    assert build.versions == ["Java 1.8.0", "Java 1.15.2"]
    versions.resolve_spec.assert_awaited_once_with("1.8+, !1.16+")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(".25 op", 5), ("0cl", 0), ("~0.4op", 8), (".25 op + .8 reset", None), ("0cl/0.3seam", None)],
)
async def test_numeric_edge_shorthand_is_seconds_without_combining_reset(value: str, expected: int | None) -> None:
    service = BuildInferenceService(
        FakeTextGenerator(InferenceResult(builds=[inferred(opening_time={"value": value, "qualifier": None})])),
        FakeTaxonomy(),
        FakeVersions(),
        "system prompt",
    )

    (build,) = await service.infer(source(), model="test")

    assert build.normal_opening_time == expected


async def test_reset_only_qualification_does_not_become_a_movement_duration() -> None:
    service = BuildInferenceService(
        FakeTextGenerator(
            InferenceResult(builds=[inferred(closing_time={"value": None, "qualifier": "10s reset on closing"})])
        ),
        FakeTaxonomy(),
        FakeVersions(),
        "system prompt",
    )

    (build,) = await service.infer(source(), model="test")

    assert build.normal_closing_time is None
    assert "10s reset on closing" in (build.description or "")
