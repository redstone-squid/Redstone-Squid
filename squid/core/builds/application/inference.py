"""Infer build submissions from messages, context, and attached images."""

import logging
import re
from collections import Counter
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, replace
from html import escape, unescape
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field
from whenever import Instant

from squid.core.builds.application.inference_expansion import InferredDoorExpansion
from squid.core.builds.domain import (
    BuildCategory,
    BuildInput,
    DoorDraftDetails,
    DoorOrientationLiteral,
    ExtenderDraftDetails,
    RestrictionField,
    RestrictionTypeLiteral,
    UnresolvedTags,
    parse_time_string,
)
from squid.core.builds.domain.credits import Credit, CreditAttribution, CreditKind, CreditRole
from squid.core.builds.models import BuildLink
from squid.core.concurrency import run_all_awaitables
from squid.core.errors import ValidationError
from squid.core.messages.domain import MessageSnapshot
from squid.core.versions.errors import InvalidVersionError

logger = logging.getLogger(__name__)
_PAIR_DURATION = (
    r"(?:instant(?:ly|aneous)?|(?:\d+(?:\.\d+)?|\.\d+)(?:[ \t]*(?:seconds?|secs?|minutes?|mins?|ms|s|[gr]t))?)"
)
_TIMING_PAIR = re.compile(
    rf"(?<![\w./])(?P<first>{_PAIR_DURATION})[ \t]*/[ \t]*(?P<second>{_PAIR_DURATION})(?![\w./])", re.IGNORECASE
)
_TIMING_CONTEXT = re.compile(
    r"\b(?:open(?:ing)?|clos(?:e|es|ing)|extension|retraction|seamless|reset|clockable|speed|instant)\b", re.IGNORECASE
)
_SOURCE_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
_SOURCE_EMPHASIS = re.compile(r"(?<!\\)(?P<marker>\*\*|__)(?P<body>\S(?:.*?\S)?)(?<!\\)(?P=marker)")
_SOURCE_OUTDATED = re.compile(r"^[\s*_]*(\[OUTDATED\]|\(OUTDATED\))", re.IGNORECASE)


def _without_source_emphasis(text: str) -> tuple[str, list[int]]:
    """Remove paired emphasis markers while retaining each character's source position."""
    skipped: set[int] = set()
    for match in _SOURCE_EMPHASIS.finditer(text):
        skipped.update(range(match.start(), match.start("body")))
        skipped.update(range(match.end("body"), match.end()))
    positions = [index for index in range(len(text)) if index not in skipped]
    return "".join(text[index] for index in positions), positions


def _source_excerpt(text: str, source: str) -> str | None:
    """Recover an original source span after escaping, quote-style, or paired emphasis changes."""
    excerpt = " ".join(text.split())
    for candidate in (excerpt, " ".join(unescape(text).split())):
        if not candidate:
            continue
        if candidate in source:
            return candidate
        start = source.translate(_SOURCE_QUOTES).find(candidate.translate(_SOURCE_QUOTES))
        if start >= 0:
            # Quote folding preserves character positions; publish the source's original punctuation.
            return source[start : start + len(candidate)]
        plain_source, positions = _without_source_emphasis(source)
        plain_candidate, _ = _without_source_emphasis(candidate)
        if not plain_candidate:
            continue
        start = plain_source.translate(_SOURCE_QUOTES).find(plain_candidate.translate(_SOURCE_QUOTES))
        if start >= 0:
            return source[positions[start] : positions[start + len(plain_candidate) - 1] + 1]
    return None


def _inferred_ticks(value: str | None) -> int | None:
    """Read instant measurements and numeric op/cl shorthand without interpreting other prose."""
    if value is not None:
        normalized = value.strip().casefold()
        if re.fullmatch(
            r"(?:insta|instant|instantly|instantaneous)(?:\s+(?:open|opening|close|closing|extension|retraction))?",
            normalized,
        ):
            return 0
        # Discord titles sometimes attach an edge label to a bare duration in seconds.
        if match := re.fullmatch(r"([~≈]?\s*(?:\d+(?:\.\d+)?|\.\d+))\s*(?:op|cl)", normalized):
            return parse_time_string(match[1])
    return parse_time_string(value)


class InferredTiming(BaseModel):
    """One measured duration and any qualifications attached to that measurement."""

    model_config = ConfigDict(extra="forbid")

    value: str | None = Field(description="One duration with a unit, such as 1.35s or 27gt; no prose or arithmetic.")
    qualifier: str | None = Field(
        description=(
            "Source-stated input delays, reset/seamless timings, estimates, or caveats; "
            "never add these to the duration or repeat equivalent units."
        )
    )


class InferredLink(BaseModel):
    """A source URL and its media type when the text establishes one."""

    model_config = ConfigDict(extra="forbid")

    url: str
    media_type: Literal["image", "video", "world-download"] | None


class InferredAuthorNote(BaseModel):
    """An additional build fact quoted from one of its source messages."""

    model_config = ConfigDict(extra="forbid")

    source_message_id: int
    text: str = Field(description="A verbatim excerpt from that primary message; never a summary or a computed change.")


class InferredCredit(BaseModel):
    """A source-stated contribution, without guessing the identity's namespace."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="Source-stated name or alias next to the mention, rather than today's Discord display name."
    )
    kind: CreditKind
    role: CreditRole
    source_message_id: int
    username: str | None = None
    subject: str | None = None


class InferredBuild(BaseModel):
    """One build described by some of the primary messages in a bundle."""

    model_config = ConfigDict(extra="forbid")

    source_message_ids: list[int]
    build_category: Literal["Piston Door", "Entrance", "Piston Extender", "Utility"] | None = Field(
        description="Category of the whole primary build. A standalone feedtape is Utility; a piston door using one is Piston Door. "
        "Unrelated preceding doors do not establish this build's category. "
        "Terse posts can still describe builds. Null when the mechanism cannot be identified."
    )
    component_restrictions: list[str] = Field(
        description="Verbatim source component constraints. Contained alone does not claim contained slime blocks; "
        "non-sticky piston-less does not mean no sticky pistons."
    )
    wiring_placement_restrictions: list[str] = Field(
        description="Verbatim source wiring constraints. Never strengthen bare seamless or hipster "
        "to full seamless or full wall hipster."
    )
    animated_restrictions: list[str]
    miscellaneous_restrictions: list[str]
    door_type: list[str]
    door_orientation: Literal["Normal", "Skydoor", "Trapdoor"] | None
    door_width: int | None = Field(description="Opening width, including the horizontal opening plane of a trapdoor.")
    door_height: int | None = Field(
        description="Opening height (second opening dimension for a trapdoor), never the enclosing circuit height."
    )
    door_depth: int | None
    build_width: int | None = Field(description="Current enclosing width, excluding proposed target measurements.")
    build_height: int | None = Field(
        description="Current enclosing circuit height, including a trapdoor stated to be 3 high despite a 2x2 opening."
    )
    build_depth: int | None = Field(description="Current enclosing depth, excluding proposed target measurements.")
    opening_time: InferredTiming | None = Field(
        description=(
            "Full/true opening movement duration; never a reset, input delay, or visible duration. "
            "For a reset-only measurement, leave value null and preserve its text in qualifier."
        )
    )
    closing_time: InferredTiming | None = Field(
        description=(
            "Full/true closing movement duration; never a reset, input delay, or visible duration. "
            "For a reset-only measurement, leave value null and preserve its text in qualifier."
        )
    )
    visible_opening_time: InferredTiming | None = Field(
        description="Explicitly stated visible opening duration, separately from full/true duration."
    )
    visible_closing_time: InferredTiming | None = Field(
        description="Explicitly stated visible closing duration, separately from full/true duration."
    )
    extender_orientation: Literal["Horizontal", "Upward", "Downward"] | None = None
    extension_length: int | None = Field(
        default=None,
        description=(
            "Extender stroke in blocks: double=2, triple=3, quad/quadruple=4. "
            "Folded circuit dimensions do not determine stroke length."
        ),
    )
    extension_time: InferredTiming | None = Field(default=None, description="Explicit extender extension duration.")
    retraction_time: InferredTiming | None = Field(default=None, description="Explicit extender retraction duration.")
    credits: list[InferredCredit] = Field(
        description="Every explicit attribution, including MC Username or IGN labels."
    )
    version_spec: str | None = Field(
        description=(
            "Working Minecraft releases for the build as shown, before any proposed modifications. "
            "Exclude releases that would require changes to the build. Retain explicit incompatibilities with exclusions, "
            "e.g. 1.8+, !1.16+ when it breaks in 1.16+. Never infer compatibility from component availability."
        )
    )
    edition: Literal["Java", "Bedrock"] | None = Field(
        default=None, description="Explicit Minecraft edition, even when no release number is stated."
    )
    author_notes: list[InferredAuthorNote] = Field(
        description=(
            "Verbatim primary-message excerpts containing additional concrete facts, such as required mods/plugins, "
            "other operating requirements, proposed measurements or version modifications, "
            "limitations with their conditions, measurement qualifications (including/excluding floor), record-width ranges, "
            "explicit revision changes, or credits. Never write your own prose, compare posts, "
            "or calculate changes. Omit facts captured elsewhere and promotional text. Empty when none."
        )
    )
    confidence: Literal["high", "medium", "low"]
    display_name: str | None = Field(default=None, description="An explicit build title or name from the source.")
    door_expansion: InferredDoorExpansion | None = Field(
        default=None,
        description="Source-stated expandable door parameters, domains and formula branches. Preserve raw syntax, "
        "unknown bounds and evidence; never infer a minimum, constant timing or computed record region.",
    )
    volume: str | None = Field(
        default=None,
        description="Reported current volume or size formula, excluding proposed targets; never a restriction.",
    )
    completion_time: str | None = Field(default=None, description="Explicit completion/design date, not a record date.")
    links: list[InferredLink] = Field(default_factory=list)
    review_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Only concrete conflicts or ambiguities affecting build facts or attribution. "
            "No comments about wording, hype, missing optional facts, or whether a tag is approved. Empty when none."
        ),
    )


class InferenceResult(BaseModel):
    """Structured result for a message bundle, which may contain no builds."""

    model_config = ConfigDict(extra="forbid")

    builds: list[InferredBuild]


@dataclass(frozen=True, slots=True)
class ContextIdentity:
    """Discord identity observed in a message's author or mention metadata."""

    user_id: int
    name: str
    username: str | None = None


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
    author_username: str | None = None
    mentions: tuple[ContextIdentity, ...] = ()


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


class BuildTagVocabulary(Protocol):
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
    """Turn structured model output into build drafts with recognized tag names."""

    def __init__(
        self,
        generator: StructuredGenerator,
        tags: BuildTagVocabulary,
        versions: VersionResolver,
        system_prompt: str,
    ) -> None:
        self._generator = generator
        self._tags = tags
        self._versions = versions
        self._system_prompt = system_prompt

    async def infer(
        self,
        source: BuildInferenceInput,
        *,
        model: str,
        reasoning_effort: str | None = None,
    ) -> list[BuildInput]:
        """Infer drafts from the primary messages and their supporting context.

        Discard inferred builds that reference no primary message. Keep recognized
        source messages in submission order, and preserve unknown tag names
        for review. A missing model result returns an empty list; provider and
        validation errors propagate to the caller.
        """
        vocabulary = await self._tags.inference_vocabulary()
        result = await self._generator.generate(
            f"{self._system_prompt}\n\nAPPROVED TAGS\n{vocabulary}",
            self._render_user_message(source),
            InferenceResult,
            model=model,
            images=source.images,
            reasoning_effort=reasoning_effort,
        )
        if result is None:
            return []

        primary_by_id = {message.message_id: message for message in source.primary}
        source_uses = Counter(message_id for value in result.builds for message_id in set(value.source_message_ids))
        builds: list[BuildInput] = []
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
            build = BuildInput(
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
            await self._apply_tags(build, inferred)
            await self._apply_fields(
                build,
                inferred,
                exclusive_source_ids=frozenset(
                    message.id for message in build.source_messages if source_uses[message.id] == 1
                ),
            )
            build.credits = self._credits(inferred, resolved)
            builds.append(build)
        return builds

    @staticmethod
    def _credits(inferred: InferredBuild, messages: Sequence[ContextMessage]) -> list[Credit]:
        by_id = {message.message_id: message for message in messages}
        credits: list[Credit] = []
        for value in inferred.credits:
            message = by_id.get(value.source_message_id)
            if message is None:
                continue
            subject = value.subject
            name, username = value.name, value.username
            if value.kind is CreditKind.NAME:
                # A model can mix a generic name with provider fields. Keep the
                # name without turning that inconsistency into an identity claim.
                subject = username = None
            elif value.kind is CreditKind.DISCORD:
                known = (
                    ContextIdentity(message.author_id, message.author_name, message.author_username),
                    *message.mentions,
                )
                mentioned_ids = set(re.findall(r"<@!?(\d+)>", message.content))
                if subject is None and name.strip() in mentioned_ids:
                    subject = name.strip()
                identity = next((person for person in known if str(person.user_id) == subject), None)
                if identity is None:
                    # Deleted or unavailable users can still have an explicit mention.
                    # Preserve its ID without inventing a current Discord username.
                    if subject in mentioned_ids:
                        username = None
                    else:
                        subject = None
                else:
                    # An old post may use an alias while Discord supplies today's
                    # display name. Both can refer to the same observed ID.
                    source_name = value.name.strip()
                    name = (
                        source_name
                        if source_name.casefold() not in {"", "me", "myself", "i", "author", "sender"}
                        and source_name in message.content
                        else identity.name
                    )
                    username = identity.username
            elif subject is not None and subject not in message.content:
                subject = None
            if username is not None:
                username = username.strip() or None
                if username is not None and len(username) > 255:
                    username = None
            try:
                credit = Credit(
                    name=name,
                    kind=value.kind,
                    role=value.role,
                    username=username,
                    source_message_id=message.message_id,
                )
            except ValidationError:
                logger.warning("Discarded invalid credit name for message %s", message.message_id)
                continue
            if subject is not None:
                try:
                    credit = replace(credit, subject=subject)
                except ValidationError:
                    logger.warning("Discarded invalid credit subject for message %s", message.message_id)
            if credit not in credits:
                credits.append(credit)
        if not any(credit.role in {CreditRole.CREATOR, CreditRole.DESIGNER} for credit in credits):
            sender = messages[0]
            credits.insert(
                0,
                Credit(
                    name=sender.author_name,
                    username=sender.author_username,
                    kind=CreditKind.DISCORD,
                    subject=str(sender.author_id),
                    attribution=CreditAttribution.ASSUMED_SENDER,
                    source_message_id=sender.message_id,
                ),
            )
        return credits

    @staticmethod
    def _render_user_message(source: BuildInferenceInput) -> str:
        def render(message: ContextMessage) -> str:
            attributes = {
                "id": str(message.message_id),
                "author": message.author_name,
                "author_id": str(message.author_id),
                "author_username": message.author_username or "",
                "mentions": "; ".join(
                    f"{person.user_id}: {person.username or person.name} ({person.name})" for person in message.mentions
                ),
                "kind": message.kind,
                "timestamp": message.timestamp,
                "attachments": message.attachment_summary,
            }
            rendered_attributes = " ".join(f'{key}="{escape(value, quote=True)}"' for key, value in attributes.items())
            return f"  <message {rendered_attributes}>{escape(message.content)}</message>"

        primary = "\n".join(render(message) for message in source.primary)
        context = "\n".join(render(message) for message in source.context)
        return f"<messages>\n <primary>\n{primary}\n </primary>\n <context>\n{context}\n </context>\n</messages>"

    async def _apply_tags(self, build: BuildInput, inferred: InferredBuild) -> None:
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
                validations.append(self._tags.validate_restrictions(values, restriction_type))

        if inferred.door_type:
            destinations.append("door_types")
            validations.append(self._tags.validate_door_types(inferred.door_type))

        # Finish or cancel all validation calls before leaving this inference step.
        results = await run_all_awaitables(validations)
        # Keep unrecognized names: an automatic pipeline may submit this draft unreviewed.
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
        UnresolvedTags.read(build.extra_info).add(
            restrictions=unresolved_restrictions, patterns=unresolved_patterns
        ).write(build.extra_info)

    async def _apply_fields(
        self, build: BuildInput, inferred: InferredBuild, *, exclusive_source_ids: frozenset[int] = frozenset()
    ) -> None:
        categories = {
            "Piston Door": BuildCategory.DOOR,
            "Entrance": BuildCategory.ENTRANCE,
            "Piston Extender": BuildCategory.EXTENDER,
            "Utility": BuildCategory.UTILITY,
        }
        build.select_category(categories[inferred.build_category] if inferred.build_category is not None else None)
        details = build.details
        build.dimensions = (inferred.build_width, inferred.build_height, inferred.build_depth)
        sources = {message.id: " ".join((message.content or "").split()) for message in build.source_messages}
        if isinstance(details, DoorDraftDetails):
            orientation = inferred.door_orientation
            details.door_orientation = (
                "Door" if orientation == "Normal" else cast(DoorOrientationLiteral | None, orientation)
            )
            details.door_dimensions = (inferred.door_width, inferred.door_height, inferred.door_depth)
            if inferred.door_expansion is not None:
                details.door_expansion = inferred.door_expansion.to_domain(sources)
        elif isinstance(details, ExtenderDraftDetails):
            details.extender_orientation = inferred.extender_orientation
            details.extension_length = inferred.extension_length
        notes: list[str] = []
        for note in inferred.author_notes:
            excerpt = _source_excerpt(note.text, sources.get(note.source_message_id, ""))
            if excerpt is None:
                logger.warning("Discarded unsupported author note for message %s", note.source_message_id)
                continue
            if excerpt not in notes:
                notes.append(excerpt)
        quoted_notes = tuple(notes)
        represented_ticks: Counter[int] = Counter()
        for label, timing, attribute in (
            ("Opening", inferred.opening_time, "normal_opening_time"),
            ("Closing", inferred.closing_time, "normal_closing_time"),
            ("Visible opening", inferred.visible_opening_time, "visible_opening_time"),
            ("Visible closing", inferred.visible_closing_time, "visible_closing_time"),
            ("Extension", inferred.extension_time, "extension_time"),
            ("Retraction", inferred.retraction_time, "retraction_time"),
        ):
            if timing is None:
                continue
            parsed = _inferred_ticks(timing.value)
            target = ExtenderDraftDetails if attribute in {"extension_time", "retraction_time"} else DoorDraftDetails
            if isinstance(details, target):
                setattr(details, attribute, parsed)
            if parsed is not None:
                represented_ticks[parsed] += 1
            approximate = timing.value is not None and re.match(r"\s*[~≈]", timing.value) is not None
            timing_category = (
                BuildCategory.EXTENDER if attribute in {"extension_time", "retraction_time"} else BuildCategory.DOOR
            )
            unrepresented = timing.value is not None and build.category is not timing_category
            if timing.qualifier or approximate or unrepresented or (timing.value is not None and parsed is None):
                # Keep qualifications and unparseable measurements through form prefill,
                # including durations without a timing field in the selected category.
                # Prefill carries the description but not arbitrary extra_info metadata.
                parts = [" ".join(part.split()) for part in (timing.value, timing.qualifier) if part]
                if not any(
                    label.casefold() in note.casefold() and all(part in note for part in parts) for note in quoted_notes
                ):
                    notes.append(f"{label}: {'; '.join(parts)}")
        # Keep source statuses and unlabelled timing pairs when the model omits them.
        # Shared source messages cannot safely attribute these facts to a variant.
        for message in build.source_messages:
            if message.id not in exclusive_source_ids:
                continue
            content = message.content or ""
            if match := _SOURCE_OUTDATED.match(content):
                marker = match[1]
                if not any(marker.casefold() in text.casefold() for text in (*notes, inferred.display_name or "")):
                    notes.append(marker)
            for line in content.splitlines():
                if _TIMING_CONTEXT.search(line) is None:
                    continue
                for pair in _TIMING_PAIR.finditer(line):
                    pair_ticks = Counter(
                        value for side in ("first", "second") if (value := _inferred_ticks(pair[side])) is not None
                    )
                    if pair_ticks.total() == 2 and not pair_ticks - represented_ticks:
                        continue
                    excerpt = " ".join(pair[0].split())
                    if not any(excerpt in note for note in notes):
                        notes.append(excerpt)
        build.version_spec = (inferred.version_spec or "").strip() or None
        if inferred.edition is not None:
            if build.version_spec is None:
                build.version_spec = inferred.edition
            elif not build.version_spec.startswith(("Java", "Bedrock")):
                build.version_spec = f"{inferred.edition} {build.version_spec}"
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
            try:
                parsed_url = urlsplit(link.url)
                valid_url = (
                    parsed_url.scheme in {"https", "http"}
                    and bool(parsed_url.hostname)
                    and not any(
                        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in link.url
                    )
                )
            except ValueError:
                valid_url = False
            if not valid_url:
                continue
            if not any(link.url in (message.content or "") for message in build.source_messages):
                continue
            if link.media_type is not None and all(existing.url != link.url for existing in build.links):
                build.links.append(BuildLink(link.url, link.media_type))
            # Untyped references have no media field, so keep their URLs in the description too.
            if not any(link.url in note for note in notes):
                notes.append(f"Reference: {link.url}")
        if inferred.review_notes:
            # Keep model commentary available in retained candidates, outside the author's description.
            build.extra_info["inference_review_notes"] = list(inferred.review_notes)
        build.description = "\n".join(notes) or None
