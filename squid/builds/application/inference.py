"""Infer build submissions from contextual message bundles, naming no chat client."""

import logging
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from html import escape
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict

from squid.builds.domain import (
    BuildCategory,
    BuildDraft,
    DoorOrientationLiteral,
    RestrictionTypeLiteral,
    SourceMessage,
    UnknownRestrictions,
    parse_time_string,
)
from squid.core.concurrency import run_all_awaitables
from squid.versions.domain import Edition

logger = logging.getLogger(__name__)


class InferredBuild(BaseModel):
    """One build described by some of the primary messages in a bundle."""

    model_config = ConfigDict(extra="forbid")

    source_message_ids: list[int]
    build_category: Literal["Piston Door", "Entrance", "Piston Extender", "Utility"] | None
    component_restrictions: list[str]
    wiring_placement_restrictions: list[str]
    animated_restrictions: list[str]
    miscellaneous_restrictions: list[str]
    door_type: list[str]
    door_orientation: Literal["Normal", "Skydoor", "Trapdoor"] | None
    door_width: int | None
    door_height: int | None
    door_depth: int | None
    build_width: int | None
    build_height: int | None
    build_depth: int | None
    opening_time: str | None
    closing_time: str | None
    creators: list[str]
    version_spec: str | None
    author_note: str | None
    confidence: Literal["high", "medium", "low"]


class InferenceResult(BaseModel):
    """Structured result for a message bundle, which may contain no builds."""

    model_config = ConfigDict(extra="forbid")

    builds: list[InferredBuild]


@dataclass(frozen=True, slots=True)
class ContextMessage:
    """One normalized Discord message supplied to inference."""

    message_id: int
    author_name: str
    author_id: int
    content: str
    timestamp: str
    kind: Literal["primary", "reply_parent", "preceding"]
    attachment_summary: str = ""


@dataclass(frozen=True, slots=True)
class InlineImage:
    """Image bytes supplied inline to a multimodal model."""

    data: bytes
    content_type: str
    source_message_id: int
    origin: Literal["attachment", "video_frame"]


@dataclass(frozen=True, slots=True)
class BuildInferenceInput:
    """Contextual message facts required to infer builds, independent of Discord objects."""

    primary: tuple[ContextMessage, ...]
    context: tuple[ContextMessage, ...]
    images: tuple[InlineImage, ...]
    channel_id: int
    server_id: int | None

    @classmethod
    def from_single_message(
        cls,
        *,
        author_name: str,
        content: str,
        message_id: int,
        author_id: int,
        channel_id: int,
        server_id: int | None,
        timestamp: str = "",
        attachment_summary: str = "",
        images: Sequence[InlineImage] = (),
    ) -> BuildInferenceInput:
        """Create the common one-primary-message form used by simple callers."""
        message = ContextMessage(
            message_id=message_id,
            author_name=author_name,
            author_id=author_id,
            content=content,
            timestamp=timestamp,
            kind="primary",
            attachment_summary=attachment_summary,
        )
        return cls((message,), (), tuple(images), channel_id, server_id)


class StructuredGenerator(Protocol):
    """Generate and validate a structured model response."""

    async def generate[T: BaseModel](
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        model: str,
        images: Sequence[InlineImage] = (),
        reasoning_effort: str | None = None,
    ) -> T | None: ...


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
    """Orchestrate structured generation and domain validation for inferred builds."""

    def __init__(
        self,
        generator: StructuredGenerator,
        taxonomy: BuildTaxonomy,
        versions: VersionResolver,
        system_prompt: str,
    ) -> None:
        self._generator = generator
        self._taxonomy = taxonomy
        self._versions = versions
        self._system_prompt = system_prompt

    async def infer(
        self,
        source: BuildInferenceInput,
        *,
        model: str,
        reasoning_effort: str | None = None,
    ) -> list[BuildDraft]:
        """Infer zero or more builds from a normalized message bundle."""
        result = await self._generator.generate(
            self._system_prompt,
            self._render_user_message(source),
            InferenceResult,
            model=model,
            images=source.images,
            reasoning_effort=reasoning_effort,
        )
        if result is None:
            return []

        primary_by_id = {message.message_id: message for message in source.primary}
        builds: list[BuildDraft] = []
        for inferred in result.builds:
            resolved = [
                primary_by_id[message_id] for message_id in inferred.source_message_ids if message_id in primary_by_id
            ]
            resolved = list(dict.fromkeys(resolved))
            if not resolved:
                logger.debug(
                    "Discarding inferred build with no valid primary source ids: %s", inferred.source_message_ids
                )
                continue
            resolved.sort(key=lambda message: source.primary.index(message))
            # Every primary message is retained, not just the first: a bundle routinely
            # spans a body message plus follow-up images, and each keeps its own content
            # rather than being concatenated into one.
            build = BuildDraft(
                source_messages=tuple(
                    SourceMessage(
                        message_id=message.message_id,
                        guild_id=source.server_id,
                        channel_id=source.channel_id,
                        author_id=message.author_id,
                        content=message.content,
                    )
                    for message in resolved
                ),
                ai_generated=True,
            )
            await self._apply_taxonomy(build, inferred)
            await self._apply_fields(build, inferred)
            builds.append(build)
        return builds

    @staticmethod
    def _render_user_message(source: BuildInferenceInput) -> str:
        def render(message: ContextMessage) -> str:
            attributes = {
                "id": str(message.message_id),
                "author": message.author_name,
                "author_id": str(message.author_id),
                "kind": message.kind,
                "timestamp": message.timestamp,
                "attachments": message.attachment_summary,
            }
            rendered_attributes = " ".join(f'{key}="{escape(value, quote=True)}"' for key, value in attributes.items())
            return f"  <message {rendered_attributes}>{escape(message.content)}</message>"

        primary = "\n".join(render(message) for message in source.primary)
        context = "\n".join(render(message) for message in source.context)
        return f"<messages>\n <primary>\n{primary}\n </primary>\n <context>\n{context}\n </context>\n</messages>"

    async def _apply_taxonomy(self, build: BuildDraft, inferred: InferredBuild) -> None:
        validations: list[Awaitable[tuple[list[str], list[str]]]] = []
        destinations: list[str] = []
        restriction_fields: tuple[tuple[list[str], RestrictionTypeLiteral, str], ...] = (
            (inferred.component_restrictions, "component", "component"),
            (inferred.wiring_placement_restrictions, "wiring-placement", "wiring"),
            (inferred.animated_restrictions, "animated", "animated"),
            (inferred.miscellaneous_restrictions, "miscellaneous", "miscellaneous"),
        )
        for values, restriction_type, destination in restriction_fields:
            if values:
                destinations.append(destination)
                validations.append(self._taxonomy.validate_restrictions(values, restriction_type))

        if inferred.door_type:
            destinations.append("door_types")
            validations.append(self._taxonomy.validate_door_types(inferred.door_type))

        # A task group rather than gather: these run on a shared session, so a
        # sibling left running after the first failure would use it concurrently.
        results = await run_all_awaitables(validations)
        unknown = UnknownRestrictions()
        build.extra_info["unknown_restrictions"] = unknown
        for destination, (recognized, unrecognized) in zip(destinations, results, strict=True):
            if destination == "component":
                build.component_restrictions = recognized
                unknown["component_restrictions"] = unrecognized
            elif destination == "wiring":
                build.wiring_placement_restrictions = recognized
                unknown["wiring_placement_restrictions"] = unrecognized
            elif destination == "animated":
                build.animated_restrictions = recognized
                unknown["animated_restrictions"] = unrecognized
            elif destination == "miscellaneous":
                build.miscellaneous_restrictions = recognized
                unknown["miscellaneous_restrictions"] = unrecognized
            else:
                build.patterns = recognized
                build.extra_info["unknown_patterns"] = unrecognized

    async def _apply_fields(self, build: BuildDraft, inferred: InferredBuild) -> None:
        categories = {
            "Piston Door": BuildCategory.DOOR,
            "Entrance": BuildCategory.ENTRANCE,
            "Piston Extender": BuildCategory.EXTENDER,
            "Utility": BuildCategory.UTILITY,
        }
        build.category = categories[inferred.build_category] if inferred.build_category is not None else None
        orientation = inferred.door_orientation
        build.door_orientation = (
            "Door" if orientation is None or orientation == "Normal" else cast(DoorOrientationLiteral, orientation)
        )
        build.door_dimensions = (inferred.door_width, inferred.door_height, inferred.door_depth)
        build.dimensions = (inferred.build_width, inferred.build_height, inferred.build_depth)
        build.normal_opening_time = parse_time_string(inferred.opening_time)
        build.normal_closing_time = parse_time_string(inferred.closing_time)
        build.creators_ign = inferred.creators
        build.version_spec = inferred.version_spec or await self._versions.newest("Java")
        build.versions = await self._versions.resolve_spec(build.version_spec)
        if inferred.author_note is not None:
            build.extra_info["user"] = inferred.author_note
