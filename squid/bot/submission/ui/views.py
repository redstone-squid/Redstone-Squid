"""Semantic submission and build-edit workspaces."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.attachment_enrichment import (
    AttachmentLifecycle,
    compact_failure_summary,
    default_only_usable,
    primary_schematic,
    select_primary,
)
from squid.bot.submission.input import optional_text, parse_web_urls, split_values
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.ui.fields import (
    BoundBuildField,
    BuildFieldSpec,
    CreationFieldSpec,
    FieldDisplay,
    field_spec,
)
from squid.bot.ui import DISCORD_YELLOW, tr
from squid.bot.utils.sentinel import DEFAULT, DefaultType
from squid.builds.application import BuildEditPatch, BuildService
from squid.builds.domain import DOOR_ORIENTATION_NAMES, Build, BuildCategory, BuildDraft, Status
from squid.builds.errors import BuildRevisionMismatchError
from squid.topics import resource_topic
from squid_ui_discord.sessions import AdmissionSpec, Reject

_DOOR_ONLY = frozenset({BuildCategory.DOOR})

EDIT_FIELDS: tuple[BuildFieldSpec, ...] = (
    field_spec("dimensions", "Width x Height x Depth", required=True),
    field_spec("door_dimensions", "2x2", required=True, categories=_DOOR_ONLY),
    field_spec("version_spec", "1.16 - 1.17.3"),
    field_spec("door_type", "Full lamp, Funnel"),
    field_spec("door_orientation_type", "Door, Trapdoor, Skydoor", categories=_DOOR_ONLY),
    field_spec("wiring_placement_restrictions", "Seamless, Full Flush"),
    field_spec("animated_restrictions", "Symmetrical, Full Sync"),
    field_spec("component_restrictions", "Observerless"),
    field_spec("miscellaneous_restrictions", "Directional, Locational"),
    field_spec("normal_closing_time", "in gameticks", categories=_DOOR_ONLY),
    field_spec("normal_opening_time", "in gameticks", categories=_DOOR_ONLY),
    field_spec("creators_ign", "Me, My Dog"),
    field_spec("image_urls", "any urls, comma separated", parser=parse_web_urls),
    field_spec("video_urls", "any urls, comma separated", parser=parse_web_urls),
    field_spec("world_download_urls", "any urls, comma separated", parser=parse_web_urls),
    field_spec("completion_time", "Any time format works"),
    field_spec("extra_user_info", "Anything a reader should know", display=FieldDisplay.PARAGRAPH),
    field_spec("server_ip", "play.example.com"),
    field_spec("coordinates", "x y z"),
    field_spec("command_to_get_to_build", "/warp door"),
)
"""Every entry must name a BuildEditPatch field; a test pins that."""


def _format_dimensions(value: tuple[int | None, ...]) -> str:
    """Format only dimensions that have actually been supplied."""
    if not any(item is not None for item in value):
        return ""
    return " x ".join("?" if item is None else str(item) for item in value)


def _parse_door_dimensions(value: str) -> tuple[int | None, int | None, int | None]:
    dimensions = parse_hallway_dimensions(value)
    if dimensions[0] is None or dimensions[1] is None:
        msg = "Enter at least a door width and height, such as 2x2."
        raise ValueError(msg)
    return dimensions


def _parse_optional_dimensions(value: str) -> tuple[int | None, int | None, int | None]:
    return parse_dimensions(value) if value.strip() else (None, None, None)


def _format_optional_text(value: str | None) -> str:
    return value or ""


def _all_restrictions(build: BuildDraft) -> list[str]:
    return [
        *build.wiring_placement_restrictions,
        *build.animated_restrictions,
        *build.component_restrictions,
        *build.miscellaneous_restrictions,
    ]


async def _set_door_dimensions(
    build: BuildDraft,
    value: tuple[int | None, int | None, int | None],
    _builds: BuildService,
) -> None:
    build.door_dimensions = value


async def _set_patterns(build: BuildDraft, value: list[str], _builds: BuildService) -> None:
    build.patterns = value


async def _set_dimensions(
    build: BuildDraft,
    value: tuple[int | None, int | None, int | None],
    _builds: BuildService,
) -> None:
    build.dimensions = value


async def _set_version(build: BuildDraft, value: str | None, _builds: BuildService) -> None:
    build.version_spec = value


async def _set_creators(build: BuildDraft, value: list[str], _builds: BuildService) -> None:
    build.creators_ign = value


async def _set_restrictions(build: BuildDraft, value: list[str], builds: BuildService) -> None:
    await builds.classify_restrictions(build, value)


def _link_target(
    media_type: Literal["image", "video", "world-download"],
) -> Callable[[BuildDraft, list[str], BuildService], Awaitable[None]]:
    async def apply(build: BuildDraft, value: list[str], _builds: BuildService) -> None:
        build.replace_links(media_type, value)

    return apply


async def _set_notes(build: BuildDraft, value: str | None, _builds: BuildService) -> None:
    if value:
        build.extra_info["user"] = value
    else:
        build.extra_info.pop("user", None)


DOOR_SIZE_FIELD = CreationFieldSpec(
    "door_size",
    "Door opening size",
    "For example: 2x2",
    _parse_door_dimensions,
    _format_dimensions,
    lambda build: build.door_dimensions,
    _set_door_dimensions,
    required=True,
    maximum=100,
)
PATTERN_FIELD = CreationFieldSpec(
    "pattern",
    "Pattern",
    "For example: regular, full lamp",
    lambda value: split_values(value) or ["Regular"],
    lambda values: ", ".join(values),
    lambda build: build.patterns,
    _set_patterns,
    maximum=500,
)
DIMENSIONS_FIELD = CreationFieldSpec(
    "dimensions",
    "Overall build size",
    "Width x Height x Depth",
    _parse_optional_dimensions,
    _format_dimensions,
    lambda build: build.dimensions,
    _set_dimensions,
    maximum=100,
)
VERSIONS_FIELD = CreationFieldSpec(
    "versions",
    "Supported versions",
    "For example: 1.20.4+",
    optional_text,
    _format_optional_text,
    lambda build: build.version_spec,
    _set_version,
    maximum=200,
)
CREATORS_FIELD = CreationFieldSpec(
    "creators",
    "Creators",
    "Minecraft names, comma separated",
    split_values,
    lambda values: ", ".join(values),
    lambda build: build.creators_ign,
    _set_creators,
    maximum=500,
)
RESTRICTIONS_FIELD = CreationFieldSpec(
    "restrictions",
    "Restrictions",
    "For example: Seamless, Observerless",
    split_values,
    lambda values: ", ".join(values),
    _all_restrictions,
    _set_restrictions,
    maximum=1000,
)
IMAGE_URLS_FIELD = CreationFieldSpec(
    "image_urls",
    "Images",
    "Image links, comma separated",
    parse_web_urls,
    lambda values: ", ".join(values),
    lambda build: list(build.image_urls),
    _link_target("image"),
    maximum=4000,
)
VIDEO_URLS_FIELD = CreationFieldSpec(
    "video_urls",
    "Videos",
    "Video links, comma separated",
    parse_web_urls,
    lambda values: ", ".join(values),
    lambda build: list(build.video_urls),
    _link_target("video"),
    maximum=4000,
)
WORLD_URLS_FIELD = CreationFieldSpec(
    "world_urls",
    "World downloads",
    "World download links, comma separated",
    parse_web_urls,
    lambda values: ", ".join(values),
    lambda build: list(build.world_download_urls),
    _link_target("world-download"),
    maximum=4000,
)
NOTES_FIELD = CreationFieldSpec(
    "notes",
    "Notes",
    "Anything staff should know",
    optional_text,
    _format_optional_text,
    lambda build: cast(str | None, build.extra_info.get("user")),
    _set_notes,
    maximum=4000,
    display=FieldDisplay.PARAGRAPH,
)

BASICS_FIELDS = (DOOR_SIZE_FIELD, PATTERN_FIELD, DIMENSIONS_FIELD, VERSIONS_FIELD, CREATORS_FIELD)
DETAIL_FIELDS = (RESTRICTIONS_FIELD, IMAGE_URLS_FIELD, VIDEO_URLS_FIELD, WORLD_URLS_FIELD, NOTES_FIELD)


def _creation_form(
    title: sl.TextLike,
    fields: Sequence[CreationFieldSpec[Any]],
    build: BuildDraft,
) -> sl.forms.FormSpec:
    def validate(values: Mapping[str, object]) -> tuple[sl.forms.FormIssue, ...]:
        errors: list[sl.forms.FormIssue] = []
        for field in fields:
            try:
                field.parse(values[field.key])
            except ValueError as error:
                errors.append(sl.forms.FieldError(field.key, str(error)))
        return tuple(errors)

    return sl.forms.FormSpec(
        title,
        tuple(field.form_field(build) for field in fields),
        validator=validate,
    )


def _submission_basics_form(build: BuildDraft) -> sl.forms.FormSpec:
    return _creation_form(tr("Build basics"), BASICS_FIELDS, build)


def _submission_details_form(build: BuildDraft) -> sl.forms.FormSpec:
    return _creation_form(tr("Links and optional details"), DETAIL_FIELDS, build)


async def _apply_creation_fields(
    fields: Sequence[CreationFieldSpec[Any]],
    values: Mapping[str, object],
    build: BuildDraft,
    builds: BuildService,
) -> None:
    """Parse every field before applying any typed target to the draft."""
    applications = tuple(field.prepare(values[field.key]) for field in fields)
    for apply in applications:
        await apply(build, builds)


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """The persisted build and presentation shown by a completed submission screen."""

    build: Build
    node: sl.LayoutNode[sl.ComponentsV2Target]
    delivery_complete: bool = True


class SubmissionDeliveryError(Exception):
    """Follow-up enrichment or presentation failed after persistence completed."""

    def __init__(self, outcome: SubmissionOutcome) -> None:
        super().__init__("submission delivery failed after persistence")
        self.outcome = outcome


class SubmissionScreen(sd.Screen):
    """A submission draft that ends when it is submitted, cancelled, or times out."""

    session = sd.SessionSpec(
        "build-submission",
        admission=AdmissionSpec(collision=Reject(notice=tr(t"You already have a submission draft open."))),
    )
    timeout = 300
    audience = "personal"

    validation_error: sl.TextLike | None = sl.state(None)
    submitting: bool = sl.state(default=False)
    cancelled: bool = sl.state(default=False)
    build: BuildDraft = sl.state(opaque=True)
    attachments: tuple[AttachmentLifecycle, ...] = sl.state((), opaque=True)
    outcome: SubmissionOutcome | None = sl.state(None, opaque=True, persist=False)

    def __init__(
        self,
        build: BuildDraft,
        builds: BuildService,
        *,
        attachments: Sequence[AttachmentLifecycle] = (),
        on_submit: Callable[[tuple[AttachmentLifecycle, ...]], Awaitable[SubmissionOutcome]],
    ) -> None:
        build.submission_status = Status.PENDING
        build.category = BuildCategory.DOOR
        build.patterns = build.patterns or ["Regular"]
        self.build = build
        self.builds = builds
        self.on_submit = on_submit
        self.attachments = default_only_usable(tuple(attachments))
        self._prefilled_dimensions: tuple[int, int, int] | None = None
        self._prefill_from_primary()

    @property
    def usable_schematics(self) -> tuple[AttachmentLifecycle, ...]:
        return tuple(attachment for attachment in self.attachments if attachment.usable_schematic)

    @property
    def requires_primary(self) -> bool:
        return len(self.usable_schematics) > 1 and primary_schematic(self.attachments) is None

    @property
    def is_ready(self) -> bool:
        width, height, _depth = self.build.door_dimensions
        return (
            self.build.door_orientation is not None
            and width is not None
            and height is not None
            and not self.requires_primary
        )

    def _prefill_from_primary(self) -> None:
        """Fill only an untouched build-size field from the selected schematic."""
        selected = primary_schematic(self.attachments)
        if selected is None or selected.analysis is None:
            return
        dimensions = selected.analysis.analysis.metrics.dimensions
        measured = (dimensions.width, dimensions.height, dimensions.length)
        current = self.build.dimensions
        if not any(item is not None for item in current) or current == self._prefilled_dimensions:
            self.build.dimensions = measured
            self._prefilled_dimensions = measured

    def _attachment_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = []
        if summary := compact_failure_summary(self.attachments):
            nodes.append(sl.status(tr(t"Some attachments could not be used:\n{summary}"), tone=sl.Tone.WARNING))
        usable = self.usable_schematics
        if len(usable) == 1:
            filename = usable[0].filename
            nodes.append(sl.note(tr(t"Primary schematic: `{filename}`")))
        elif len(usable) > 1:
            selected = primary_schematic(self.attachments)
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(
                            attachment.filename,
                            key=attachment.identity,
                            description=self._schematic_description(attachment),
                        )
                        for attachment in usable
                    ),
                    key="primary_schematic",
                    selection=sl.controlled(
                        (selected.identity,) if selected is not None else (),
                        self._primary_changed,
                    ),
                )
            )
        return tuple(nodes)

    @staticmethod
    def _schematic_description(attachment: AttachmentLifecycle) -> str:
        assert attachment.analysis is not None
        dimensions = attachment.analysis.analysis.metrics.dimensions
        return f"Measured {dimensions.width} x {dimensions.height} x {dimensions.length}"

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self.cancelled:
            return (sl.status(tr(t"Submission cancelled. Nothing was saved.")),)
        if self.outcome is not None:
            from squid.bot.submission.ui.controls import build_edit

            build_id = self.outcome.build.id
            assert build_id is not None, "a submitted build has a persistent id"
            headline = (
                tr(t"Submitted for review. Submission ID: {build_id}.")
                if self.outcome.delivery_complete
                else tr(
                    t"Submission {build_id} was saved, but attachment processing or review-card delivery did not finish. Staff can recover it without resubmitting."
                )
            )
            return (
                sl.status(headline, tone=sl.Tone.SUCCESS if self.outcome.delivery_complete else sl.Tone.WARNING),
                self.outcome.node,
                sl.primitives.Section(
                    (sl.primitives.Text(tr(t"Staff can now review and vote on this build."), priority=-10),),
                    sl.primitives.RoutedButton(tr(t"Edit"), build_edit.id(build_id=build_id)),
                ),
            )
        missing_door_type = self.build.door_orientation is None
        missing_opening_size = not self.build.door_width or not self.build.door_height
        guidance = self.validation_error
        if guidance is None and self.requires_primary:
            guidance = tr(t"Choose which usable schematic is primary before review.")
        elif guidance is None and missing_door_type and missing_opening_size:
            guidance = tr(t"Required before review: door type and door opening size.")
        elif guidance is None and missing_door_type:
            missing_field = "door type"
            guidance = tr(t"Required before review: {missing_field}.")
        elif guidance is None and missing_opening_size:
            missing_field = "door opening size"
            guidance = tr(t"Required before review: {missing_field}.")
        if guidance is None:
            guidance = tr(t"Ready to submit. Optional details can be added later.")
        fields = (
            sl.field(tr(t"Door type"), self.build.door_orientation or "—"),
            sl.field(tr(t"Opening size"), _format_dimensions(self.build.door_dimensions) or "—"),
            sl.field(tr(t"Pattern"), ", ".join(self.build.patterns)),
            sl.field(tr(t"Build size"), _format_dimensions(self.build.dimensions) or "—"),
            sl.field(tr(t"Versions"), self.build.version_spec or "—"),
            sl.field(tr(t"Creators"), ", ".join(self.build.creators_ign) or "—"),
        )
        return (
            sl.section(
                sl.heading(tr(t"Submit a build")),
                sl.truncate(sl.paragraph(guidance)),
                sl.fields(*fields),
                sl.note(tr(t"Only the door type and opening size are required.")),
                accent=sl.palette.INHERIT if self.is_ready else DISCORD_YELLOW,
            ),
            *self._attachment_nodes(),
            sl.choices(
                *(sl.choice(tr(value), key=value) for value in DOOR_ORIENTATION_NAMES),
                key="door_type",
                selection=sl.controlled(
                    (self.build.door_orientation,) if self.build.door_orientation is not None else (),
                    self._door_changed,
                ),
            ),
            sl.choices(
                sl.choice(
                    tr(t"Directional"),
                    key="Directional",
                    description=tr(t"May depend on the direction it faces"),
                ),
                sl.choice(
                    tr(t"Locational"),
                    key="Locational",
                    description=tr(t"May depend on its position in the world"),
                ),
                key="location",
                selection=sl.controlled(
                    tuple(
                        value
                        for value in ("Directional", "Locational")
                        if value in self.build.miscellaneous_restrictions
                    ),
                    self._location_changed,
                ),
                minimum=0,
                maximum=2,
            ),
            sl.action_controls(
                sl.action_control(
                    tr(t"Edit basics"),
                    self._edit_basics,
                    key="edit_basics",
                    emphasis=sl.semantic.Emphasis.STRONG,
                ),
                sl.action_control(
                    tr(t"Add links & details"),
                    self._edit_details,
                    key="edit_details",
                ),
                sl.action_control(
                    tr(t"Submit for review"),
                    self._submit,
                    key="submit",
                    tone=sl.Tone.SUCCESS,
                    available=not self.submitting,
                ),
                sl.action_control(tr(t"Cancel"), self._cancel, key="cancel"),
                key="submission-actions",
            ),
        )

    async def _door_changed(self, event: sl.ChoiceEvent) -> None:
        self.build.door_orientation = cast(Literal["Door", "Skydoor", "Trapdoor"], event.selected[0])
        self.validation_error = None
        self.mutated(self.build)

    async def _location_changed(self, event: sl.ChoiceEvent) -> None:
        self.build.miscellaneous_restrictions = list(event.selected)
        self.mutated(self.build)

    async def _primary_changed(self, event: sl.ChoiceEvent) -> None:
        self.attachments = select_primary(self.attachments, event.selected[0])
        self._prefill_from_primary()
        self.validation_error = None
        self.mutated(self.build)

    async def _edit_basics(self, event: sl.PressEvent) -> None:
        await event.present_form(
            _submission_basics_form(self.build),
            key="submission-basics",
            on_submit=self._basics_submitted,
        )

    async def _basics_submitted(self, event: sl.SubmitEvent) -> None:
        await _apply_creation_fields(BASICS_FIELDS, event.values, self.build, self.builds)
        self.validation_error = None
        self.mutated(self.build)

    async def _edit_details(self, event: sl.PressEvent) -> None:
        await event.present_form(
            _submission_details_form(self.build),
            key="submission-details",
            on_submit=self._details_submitted,
        )

    async def _details_submitted(self, event: sl.SubmitEvent) -> None:
        await _apply_creation_fields(DETAIL_FIELDS, event.values, self.build, self.builds)
        self.validation_error = None
        self.mutated(self.build)

    async def _submit(self, event: sl.PressEvent) -> None:
        if self.outcome is not None:
            await event.notice(tr(t"This build has already been submitted."))
            return
        if not self.is_ready:
            self.validation_error = None
            self.invalidate()
            return
        if self.submitting:
            await event.notice(tr(t"This build is still being submitted. Give it a moment."))
            return
        self.submitting = True
        await event.acknowledge()
        try:
            self.outcome = await self.on_submit(self.attachments)
        except SubmissionDeliveryError as error:
            self.outcome = error.outcome
            self.submitting = False
            self.invalidate()
            raise
        except Exception:
            self.submitting = False
            self.validation_error = tr(
                t"Submitting failed and nothing was saved. Press **Submit for review** to try again."
            )
            self.invalidate()
            raise
        self.submitting = False
        await event.finish()

    async def _cancel(self, event: sl.PressEvent) -> None:
        self.cancelled = True
        await event.finish()


def _edit_form(items: Sequence[BoundBuildField], page: int) -> sl.forms.FormSpec:
    fields: list[sl.forms.FormField[Any]] = []
    for item in items[5 * (page - 1) : 5 * page]:
        spec = item.spec
        field_type = sl.forms.TextAreaField if spec.display is FieldDisplay.PARAGRAPH else sl.forms.TextField
        fields.append(
            field_type(
                key=item.attribute,
                label=tr(spec.label),
                placeholder=tr(spec.placeholder),
                default=item.current_text,
                required=spec.required,
                minimum=spec.minimum,
                maximum=spec.maximum,
            )
        )
    return sl.forms.FormSpec(tr("Edit build, section {page}", page=page), tuple(fields))


class BuildEditScreen(sd.Screen):
    """A build editor that ends when saved, closed, replaced, or timed out."""

    session = sd.SessionSpec("build-edit")
    timeout = 900
    audience = "personal"
    follow_topics = True
    root_options = {"retain_routed_on_timeout": True}

    page: int = sl.state(1)
    confirming: bool = sl.state(default=False)
    saved: bool = sl.state(default=False)
    validation_error: sl.TextLike | None = sl.state(None)

    def __init__(
        self,
        build: Build,
        builds: BuildService,
        items: Sequence[BoundBuildField] | DefaultType = DEFAULT,
        *,
        node: sl.LayoutNode[sl.ComponentsV2Target] | None = None,
        authorize: Callable[[], Awaitable[bool]],
        render_build: Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target]]],
        refresh_posts: Callable[[int], Awaitable[None]],
        recovered: bool = False,
    ) -> None:
        self._seed: tuple[Build, sl.LayoutNode[sl.ComponentsV2Target] | None] | None = (build, node)
        self._build_id = build.id
        self.builds = builds
        self._authorize = authorize
        self._render_build = render_build
        self._refresh_posts = refresh_posts
        self._recovered = recovered
        if items is DEFAULT:
            items = [field.bind(build) for field in EDIT_FIELDS if field.applies_to(build)]
        self.items = tuple(items)

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def projection(self) -> tuple[Build, sl.LayoutNode[sl.ComponentsV2Target] | None]:
        """Load the edited build and keep its preview current with the build topic."""
        if self._build_id is not None:
            sl.runtime.watch(resource_topic("build", str(self._build_id)))
        seed, self._seed = self._seed, None
        if seed is not None:
            return seed
        if self._build_id is None:
            message = "this editor has no way to reload itself"
            raise sl.resources.ResourceNotReadyError(message)
        latest_build = await self.builds.get(self._build_id)
        latest = None if latest_build is None else (latest_build, await self._render_build(latest_build))
        if latest is None:
            message = f"build {self._build_id} no longer exists"
            raise LookupError(message)
        return latest

    def _current(self) -> tuple[Build, sl.LayoutNode[sl.ComponentsV2Target] | None]:
        if self._seed is not None:
            return self._seed
        state = self.projection.status
        if isinstance(state, sl.resources.Ready):
            return state.value
        if state.previous is not None:
            return state.previous.value
        message = "this editor has not loaded a build yet"
        raise sl.resources.ResourceNotReadyError(message)

    @property
    def build(self) -> Build:
        return self._current()[0]

    def _replace(self, build: Build, node: sl.LayoutNode[sl.ComponentsV2Target] | None) -> None:
        if self._seed is not None:
            self._seed = (build, node)
        else:
            self.projection.replace((build, node))

    @property
    def max_pages(self) -> int:
        return max(1, (len(self.items) + 4) // 5)

    def stage(self, attribute: str, text: str) -> bool:
        for item in self.items:
            if item.attribute == attribute:
                item.stage(text)
                previous = self.validation_error if isinstance(self.validation_error, str) else None
                self.validation_error = "\n".join(error for error in (previous, item.validation_error) if error) or None
                return True
        return False

    async def may_edit(self) -> bool:
        """Recheck whether the actor may currently edit this build."""
        return await self._authorize()

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        from squid.bot.submission.ui.controls import build_edit_recovery

        if self.saved:
            return (
                sl.section(
                    sl.heading(tr(t"Changes saved")),
                    sl.paragraph(tr(t"The build card has been refreshed.")),
                ),
            )
        state = self.projection.status
        if self._seed is None and not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(tr(t"Loading build.")),)
        page = self.page
        pages = self.max_pages
        validation_error = self.validation_error
        description = (
            tr(t"Section {page} of {pages}. Filled dots have unsaved changes.")
            if not validation_error
            else tr(t"Fix these values before review:\n{validation_error}")
        )
        controls: list[sl.semantic.ActionControl] = [
            sl.action_control(tr(t"Edit this section"), self._open, key="open"),
            sl.action_control(tr(t"Previous"), self._previous, key="previous", available=self.page != 1),
            sl.action_control(tr(t"Next"), self._next, key="next", available=self.page != self.max_pages),
        ]
        if self.confirming:
            controls.extend(
                (
                    sl.action_control(
                        tr(t"Apply changes"),
                        self._apply,
                        key="apply",
                        tone=sl.Tone.SUCCESS,
                    ),
                    sl.action_control(tr(t"Back"), self._unconfirm, key="unconfirm"),
                )
            )
        else:
            controls.append(
                sl.action_control(
                    tr(t"Review changes"),
                    self._review,
                    key="review",
                    tone=sl.Tone.SUCCESS,
                )
            )
        if self.validation_error:
            controls.append(sl.action_control(tr(t"Reload latest"), self._reload, key="reload"))
        controls.append(sl.action_control(tr(t"Close"), self._close, key="close"))
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = []
        if self._recovered:
            nodes.append(
                sl.status(
                    tr(t"Fresh editor loaded. Unsaved changes from the previous editor were discarded."),
                    tone=sl.Tone.WARNING,
                )
            )
        nodes.append(
            sl.section(
                sl.heading(tr(t"Edit build")),
                sl.truncate(sl.paragraph(description)),
                sl.fields(sl.field(tr(t"Fields in this section"), self.summary_text())),
                sl.note(tr(t"Reloading a fresh editor discards every staged change in this one.")),
                accent=DISCORD_YELLOW if self.validation_error else sl.palette.INHERIT,
            )
        )
        if (node := self._current()[1]) is not None:
            nodes.append(node)
        nodes.append(
            sl.action_controls(*controls, key="build-edit-actions", display=sl.semantic.ControlDisplay.INDIVIDUAL)
        )
        if self._build_id is not None:
            nodes.append(
                sl.action_controls(
                    sl.routed_action_control(
                        tr(t"Reload fresh editor"),
                        build_edit_recovery.id(build_id=self._build_id),
                        key="restart",
                    ),
                    key="build-edit-recovery",
                )
            )
        return tuple(nodes)

    def summary_text(self) -> str:
        page_items = self.items[5 * (self.page - 1) : 5 * self.page]
        return "\n".join(f"{'●' if item.modified else '○'} {item.summary}" for item in page_items)

    async def _open(self, event: sl.PressEvent) -> None:
        if await self._may_event(event):
            await event.present_form(
                _edit_form(self.items, self.page),
                key="edit",
                on_submit=self._edited,
            )

    async def _edited(self, event: sl.SubmitEvent) -> None:
        errors: list[str] = []
        for item in self.items[5 * (self.page - 1) : 5 * self.page]:
            item.stage(cast(str, event.values[item.attribute]))
            if item.validation_error:
                errors.append(f"**{item.spec.label}:** {item.validation_error}")
        self.validation_error = "\n".join(errors) or None
        if errors:
            error_text = "\n".join(errors)
            await event.notice(tr(t"Fix these values before review:\n{error_text}"))
        self.invalidate()

    async def _previous(self, event: sl.PressEvent) -> None:
        if self.page > 1:
            self.page -= 1

    async def _next(self, event: sl.PressEvent) -> None:
        if self.page < self.max_pages:
            self.page += 1

    async def _review(self, event: sl.PressEvent) -> None:
        if not await self._may_event(event):
            return
        if self.validation_error:
            return
        if not any(item.modified for item in self.items):
            self.validation_error = tr(t"No changes to review yet.")
            return
        self.confirming = True

    async def _unconfirm(self, event: sl.PressEvent) -> None:
        self.confirming = False

    async def _apply(self, event: sl.PressEvent) -> None:
        if not await self._may_event(event):
            return
        changed = [item for item in self.items if item.modified]
        await event.acknowledge()
        patch = BuildEditPatch.from_attributes({item.attribute: item.actual_value for item in changed})
        edited_build_id: int | None = None
        build = self.build
        if build.id is None:
            patch.apply(build)
            await self.builds.save(build)
            self._build_id = build.id
        else:
            try:
                async with self.builds.edit(build.id, patch, expected_revision=build.revision) as edit:
                    build = await edit.commit()
            except BuildRevisionMismatchError:
                self.confirming = False
                self.validation_error = tr(
                    t"This build changed while you were editing. Reload the latest version; your staged changes will be discarded."
                )
                self.invalidate()
                return
            edited_build_id = build.id
        self.saved = True
        self.confirming = False
        self._replace(build, await self._render_build(build))
        await event.finish()
        if edited_build_id is not None:
            await self._refresh_posts(edited_build_id)

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()

    async def _reload(self, event: sl.PressEvent) -> None:
        await self.projection.reload()
        if not await self._may_event(event):
            return
        await event.acknowledge()
        build, node = self._current()
        self.items = tuple(field.bind(build) for field in EDIT_FIELDS if field.applies_to(build))
        self.validation_error = None
        self.confirming = False
        self._replace(build, node)

    async def _may_event(self, event: sl.ActionEvent) -> bool:
        if not await self.may_edit():
            await event.notice(tr(t"Only the pending build's submitter or a trusted staff member can edit it."))
            return False
        return True
