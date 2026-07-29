"""Build inference application tests."""

from squid.builds.application import BuildInferenceInput, BuildInferenceService
from squid.builds.domain import RestrictionTypeLiteral
from squid.versions.domain import Edition

COMPLETE_OUTPUT = """
<target>
record_category: Smallest
component_restriction: No Observers, Mystery
wiring_placement_restrictions: Seamless
miscellaneous_restrictions: none
piston_door_type: Regular, Unknown Pattern
door_orientation: Normal
door_width: 3
door_height: 4
door_depth: 1
build_width: 8
build_height: 9
build_depth: 10
opening_time: 1.0s
closing_time: 0.5s
creators: BuilderOne, BuilderTwo
version: 1.20+
image: https://example.com/image.png
author_note: first line\\nsecond line
</target>
"""


class FakeTextGenerator:
    def __init__(self, output: str | None) -> None:
        self.output = output
        self.calls: list[tuple[str, str]] = []

    async def generate(self, prompt: str, *, model: str) -> str | None:
        self.calls.append((prompt, model))
        return self.output


class FakeTaxonomy:
    async def validate_restrictions(
        self,
        restrictions: list[str],
        type: RestrictionTypeLiteral,
    ) -> tuple[list[str], list[str]]:
        recognized = [value for value in restrictions if value != "Mystery"]
        unknown = [value for value in restrictions if value == "Mystery"]
        return recognized, unknown

    async def validate_door_types(self, door_types: list[str]) -> tuple[list[str], list[str]]:
        return [value for value in door_types if value == "Regular"], [
            value for value in door_types if value != "Regular"
        ]


class FakeVersions:
    async def newest(self, edition: Edition) -> str:
        return f"{edition} 1.21.0"

    async def resolve_spec(self, version_spec: str) -> list[str]:
        return ["Java 1.20.0", "Java 1.21.0"]


def source() -> BuildInferenceInput:
    return BuildInferenceInput(
        author_name="Builder",
        content="A redstone door",
        message_id=10,
        author_id=20,
        channel_id=30,
        server_id=40,
    )


async def test_infer_maps_generated_text_and_validates_taxonomy() -> None:
    generator = FakeTextGenerator(COMPLETE_OUTPUT)
    service = BuildInferenceService(generator, FakeTaxonomy(), FakeVersions(), "Analyze: {message}")

    build = await service.infer(source(), model="test-model")

    assert build is not None
    assert generator.calls == [("Analyze: Builder wrote the following message:\nA redstone door", "test-model")]
    assert build.original_message_id == 10
    assert build.record_category == "Smallest"
    assert build.door_dimensions == (3, 4, 1)
    assert build.dimensions == (8, 9, 10)
    assert build.component_restrictions == ["No Observers"]
    unknown_restrictions = build.extra_info.get("unknown_restrictions")
    assert unknown_restrictions is not None
    assert unknown_restrictions.get("component_restrictions") == ["Mystery"]
    assert build.door_type == ["Regular"]
    assert build.extra_info.get("unknown_patterns") == ["Unknown Pattern"]
    assert build.version_spec == "1.20+"
    assert build.versions == ["Java 1.20.0", "Java 1.21.0"]
    assert build.extra_info.get("user") == "first line\nsecond line"


async def test_infer_rejects_unstructured_or_incomplete_output() -> None:
    for output in ("not a contraption", "<target>record_category: Smallest</target>", None):
        service = BuildInferenceService(FakeTextGenerator(output), FakeTaxonomy(), FakeVersions(), "{message}")
        assert await service.infer(source(), model="test-model") is None
