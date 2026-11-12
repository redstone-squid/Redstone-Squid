"""Infer build submissions from messages, context, and attached images."""

import logging
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from html import escape
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field
from whenever import Instant

from squid.builds.domain import (
    BuildCategory,
    BuildDraft,
    DoorOrientationLiteral,
    RestrictionField,
    RestrictionTypeLiteral,
    UnresolvedTaxonomy,
    parse_time_string,
)
from squid.builds.models import BuildLink
from squid.core.concurrency import run_all_awaitables
from squid.messages.domain import MessageSnapshot
from squid.versions.errors import InvalidVersionError

logger = logging.getLogger(__name__)


class InferredTiming(BaseModel):
    """One measured duration and any qualifications attached to that measurement."""

    model_config = ConfigDict(extra="forbid")

    value: str | None = Field(description="One duration with a unit, such as 1.35s or 27gt; no prose or arithmetic.")
    qualifier: str | None = Field(
        description="Source-stated input delay, estimates, or measurement caveats; not the same duration in another unit."
    )


class InferredLink(BaseModel):
    """A source URL and its media type when the text establishes one."""

    model_config = ConfigDict(extra="forbid")

    url: str
    media_type: Literal["image", "video", "world-download"] | None


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
    build_width: int | None = Field(description="Enclosing width, including an explicit WxHxD on a Volume line.")
    build_height: int | None = Field(description="Enclosing height, including an explicit WxHxD on a Volume line.")
    build_depth: int | None = Field(description="Enclosing depth, including an explicit WxHxD on a Volume line.")
    opening_time: InferredTiming | None
    closing_time: InferredTiming | None
    creators: list[str]
    version_spec: str | None
    author_note: str | None = Field(
        description=(
            "Additional concrete build facts: revision changes, operating requirements, limitations, or credits. "
            "Use concise factual prose. Omit praise, title analysis, wording or punctuation commentary, "
            "and facts already captured in other fields. Null when there is nothing useful to add."
        )
    )
    confidence: Literal["high", "medium", "low"]
    display_name: str | None = Field(default=None, description="An explicit build title or name from the source.")
    volume: str | None = Field(default=None, description="Reported volume or size formula; never a restriction.")
    completion_time: str | None = Field(default=None, description="Explicit completion/design date, not a record date.")
    links: list[InferredLink] = Field(default_factory=list)
    review_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Only concrete conflicts or ambiguities affecting build facts or attribution. "
            "No comments about wording, hype, missing optional facts, or taxonomy membership. Empty when none."
        ),
    )


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
    """Contextual message details required to infer builds, independent of Discord objects."""

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

    async def inference_vocabulary(self) -> str: ...


class VersionResolver(Protocol):
    """Resolve build version specifications."""

    async def resolve_spec(self, version_spec: str) -> list[str]: ...


class BuildInferenceService:
    """Turn structured model output into build drafts with recognized taxonomy names."""

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
        """Infer drafts from the primary messages and their supporting context.

        Discard inferred builds that reference no primary message. Keep recognized
        source messages in submission order, and preserve unknown taxonomy names
        for review. A missing model result returns an empty list; provider and
        validation errors propagate to the caller.
        """
        vocabulary = await self._taxonomy.inference_vocabulary()
        result = await self._generator.generate(
            f"{self._system_prompt}\n\nAPPROVED TAXONOMY\n{vocabulary}",
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
            # Keep each selected message separately so reviewers can trace the
            # inferred build to its text and follow-up attachments.
            build = BuildDraft(
                source_messages=tuple(
                    MessageSnapshot(
                        id=message.message_id,
                        guild_id=source.server_id,
                        channel_id=source.channel_id,
                        author_id=message.author_id,
                        content=message.content,
                        created_at=Instant.parse_iso(message.timestamp) if message.timestamp else None,
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

        # Finish or cancel all validation calls before leaving this inference step.
        results = await run_all_awaitables(validations)
        # Keep what the taxonomy rejected: an automatic pipeline may submit this draft unreviewed.
        unresolved_restrictions: list[tuple[RestrictionField, str]] = []
        unresolved_patterns: list[str] = []
        for destination, (recognized, unrecognized) in zip(destinations, results, strict=True):
            if destination == "component":
                build.component_restrictions = recognized
                unresolved_restrictions.extend(("component_restrictions", name) for name in unrecognized)
            elif destination == "wiring":
                build.wiring_placement_restrictions = recognized
                unresolved_restrictions.extend(("wiring_placement_restrictions", name) for name in unrecognized)
            elif destination == "animated":
                build.animated_restrictions = recognized
                unresolved_restrictions.extend(("animated_restrictions", name) for name in unrecognized)
            elif destination == "miscellaneous":
                build.miscellaneous_restrictions = recognized
                unresolved_restrictions.extend(("miscellaneous_restrictions", name) for name in unrecognized)
            else:
                build.patterns = recognized
                unresolved_patterns.extend(unrecognized)
        UnresolvedTaxonomy.read(build.extra_info).add(
            restrictions=unresolved_restrictions, patterns=unresolved_patterns
        ).write(build.extra_info)

    async def _apply_fields(self, build: BuildDraft, inferred: InferredBuild) -> None:
        categories = {
            "Piston Door": BuildCategory.DOOR,
            "Entrance": BuildCategory.ENTRANCE,
            "Piston Extender": BuildCategory.EXTENDER,
            "Utility": BuildCategory.UTILITY,
        }
        build.category = categories[inferred.build_category] if inferred.build_category is not None else None
        orientation = inferred.door_orientation
        build.door_orientation = "Door" if orientation == "Normal" else cast(DoorOrientationLiteral | None, orientation)
        build.door_dimensions = (inferred.door_width, inferred.door_height, inferred.door_depth)
        build.dimensions = (inferred.build_width, inferred.build_height, inferred.build_depth)
        notes = [inferred.author_note] if inferred.author_note else []
        for label, timing, attribute in (
            ("Opening", inferred.opening_time, "normal_opening_time"),
            ("Closing", inferred.closing_time, "normal_closing_time"),
        ):
            if timing is None:
                continue
            parsed = parse_time_string(timing.value)
            setattr(build, attribute, parsed)
            if timing.qualifier or (timing.value is not None and parsed is None):
                # Keep qualifications and unparseable measurements through form prefill,
                # which carries the description but not arbitrary extra_info metadata.
                notes.append(f"{label}: {'; '.join(part for part in (timing.value, timing.qualifier) if part)}")
        build.creators_ign = inferred.creators
        build.version_spec = (inferred.version_spec or "").strip() or None
        if build.version_spec is not None:
            try:
                build.versions = await self._versions.resolve_spec(build.version_spec)
            except ValueError, InvalidVersionError:
                # Keep the original claim available for correction instead of losing
                # this build and every other candidate in the same model response.
                logger.warning(
                    "Could not resolve inferred version specification %r; retained for review", build.version_spec
                )

        build.display_name = inferred.display_name
        build.completion_time = inferred.completion_time
        if inferred.volume:
            notes.append(f"Reported volume: {inferred.volume}")
        for link in inferred.links:
            if not any(link.url in (message.content or "") for message in build.source_messages):
                continue
            if link.media_type is not None and all(existing.url != link.url for existing in build.links):
                build.links.append(BuildLink(link.url, link.media_type))
            # Submission forms collect uploaded media rather than external URLs, so
            # retain references in the description as well as the candidate's links.
            if not any(link.url in note for note in notes):
                notes.append(f"Reference: {link.url}")
        notes.extend(f"Needs review: {note}" for note in inferred.review_notes)
        build.description = "\n".join(notes) or None
