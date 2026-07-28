"""Framework-neutral inference of build submissions from message text."""

import asyncio
import logging
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol, cast

from squid.db.builds import Build
from squid.db.schema import DoorOrientationLiteral, RecordCategoryLiteral, RestrictionTypeLiteral, UnknownRestrictions
from squid.services.versions import Edition
from squid.utils import parse_time_string

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = frozenset(
    {
        "record_category",
        "component_restriction",
        "wiring_placement_restrictions",
        "miscellaneous_restrictions",
        "piston_door_type",
        "door_orientation",
        "door_width",
        "door_height",
        "door_depth",
        "build_width",
        "build_height",
        "build_depth",
        "opening_time",
        "closing_time",
        "creators",
        "version",
        "image",
        "author_note",
    }
)


@dataclass(frozen=True, slots=True)
class BuildInferenceInput:
    """Message facts required to infer a build, independent of Discord objects."""

    author_name: str
    content: str
    message_id: int
    author_id: int
    channel_id: int
    server_id: int | None


class TextGenerator(Protocol):
    """Generate text from a prompt using an external model."""

    async def generate(self, prompt: str, *, model: str) -> str | None: ...


class BuildTaxonomy(Protocol):
    """Validate inferred names against recognized build metadata."""

    async def validate_restrictions(
        self,
        restrictions: list[str],
        type: RestrictionTypeLiteral,
    ) -> tuple[list[str], list[str]]: ...

    async def validate_door_types(self, door_types: list[str]) -> tuple[list[str], list[str]]: ...


class VersionResolver(Protocol):
    """Resolve build version specifications."""

    async def newest(self, edition: Edition) -> str: ...

    async def resolve_spec(self, version_spec: str) -> list[str]: ...


class BuildInferenceService:
    """Orchestrate text generation and domain validation for inferred builds."""

    def __init__(
        self,
        generator: TextGenerator,
        taxonomy: BuildTaxonomy,
        versions: VersionResolver,
        prompt_template: str,
    ) -> None:
        self._generator = generator
        self._taxonomy = taxonomy
        self._versions = versions
        self._prompt_template = prompt_template

    async def infer(self, source: BuildInferenceInput, *, model: str) -> Build | None:
        """Infer a build from normalized message facts."""
        prompt = self._prompt_template.format(
            message=f"{source.author_name} wrote the following message:\n{source.content}"
        )
        output = await self._generator.generate(prompt, model=model)
        if output is None:
            return None
        logger.debug("AI output: %s", output)

        variables = self._parse_output(output)
        if variables is None:
            return None

        build = Build(
            original_server_id=source.server_id,
            original_channel_id=source.channel_id,
            original_message_id=source.message_id,
            original_message_author_id=source.author_id,
            original_message=source.content,
            ai_generated=True,
        )
        build.record_category = cast(RecordCategoryLiteral | None, variables["record_category"])
        await self._apply_taxonomy(build, variables)
        await self._apply_fields(build, variables)
        return build

    async def _apply_taxonomy(self, build: Build, variables: dict[str, str | None]) -> None:
        validations: list[Awaitable[tuple[list[str], list[str]]]] = []
        destinations: list[str] = []
        restriction_fields: tuple[tuple[str, RestrictionTypeLiteral, str], ...] = (
            ("component_restriction", "component", "component"),
            ("wiring_placement_restrictions", "wiring-placement", "wiring"),
            ("miscellaneous_restrictions", "miscellaneous", "miscellaneous"),
        )
        for field, restriction_type, destination in restriction_fields:
            value = variables[field]
            if value is not None:
                destinations.append(destination)
                validations.append(self._taxonomy.validate_restrictions(value.split(", "), restriction_type))

        door_types = variables["piston_door_type"]
        if door_types is not None:
            destinations.append("door_types")
            validations.append(self._taxonomy.validate_door_types(door_types.split(", ")))

        results = await asyncio.gather(*validations)
        unknown = UnknownRestrictions()
        build.extra_info["unknown_restrictions"] = unknown
        for destination, (recognized, unrecognized) in zip(destinations, results, strict=True):
            if destination == "component":
                build.component_restrictions = recognized
                unknown["component_restrictions"] = unrecognized
            elif destination == "wiring":
                build.wiring_placement_restrictions = recognized
                unknown["wiring_placement_restrictions"] = unrecognized
            elif destination == "miscellaneous":
                build.miscellaneous_restrictions = recognized
                unknown["miscellaneous_restrictions"] = unrecognized
            else:
                build.door_type = recognized
                build.extra_info["unknown_patterns"] = unrecognized

    async def _apply_fields(self, build: Build, variables: dict[str, str | None]) -> None:
        orientation = variables["door_orientation"]
        build.door_orientation_type = (
            "Door" if orientation is None or orientation == "Normal" else cast(DoorOrientationLiteral, orientation)
        )
        build.door_dimensions = (
            self._optional_int(variables["door_width"]),
            self._optional_int(variables["door_height"]),
            self._optional_int(variables["door_depth"]),
        )
        build.dimensions = (
            self._optional_int(variables["build_width"]),
            self._optional_int(variables["build_height"]),
            self._optional_int(variables["build_depth"]),
        )
        build.normal_opening_time = parse_time_string(variables["opening_time"])
        build.normal_closing_time = parse_time_string(variables["closing_time"])
        build.creators_ign = self._split(variables["creators"])
        build.version_spec = variables["version"] or await self._versions.newest("Java")
        build.versions = await self._versions.resolve_spec(build.version_spec)
        build.image_urls = self._split(variables["image"])
        if variables["author_note"] is not None:
            build.extra_info["user"] = variables["author_note"].replace("\\n", "\n")

    @staticmethod
    def _parse_output(output: str) -> dict[str, str | None] | None:
        match = re.search(r"<target>(.*?)</target>", output, re.DOTALL)
        if match is None:
            return None

        variables: dict[str, str | None] = {}
        for line in match.group(1).strip().splitlines():
            if not line.strip() or ":" not in line:
                continue
            key, raw_value = line.split(":", 1)
            value = raw_value.strip()
            variables[key.strip()] = None if value.lower() in {"none", "null", "unknown"} else value

        if not _REQUIRED_FIELDS.issubset(variables):
            logger.debug("Missing keys in AI output variables")
            return None
        return variables

    @staticmethod
    def _optional_int(value: str | None) -> int | None:
        return int(value) if value else None

    @staticmethod
    def _split(value: str | None) -> list[str]:
        return value.split(", ") if value else []
