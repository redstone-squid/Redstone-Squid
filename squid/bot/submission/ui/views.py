"""Semantic submission and build-edit workspaces."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.ui.fields import BoundBuildField, BuildFieldSpec, FieldDisplay, field_spec
from squid.bot.ui import DISCORD_YELLOW, L
from squid.bot.utils.sentinel import DEFAULT, DefaultType
from squid.builds.application import BuildEditPatch, BuildService
from squid.builds.domain import DOOR_ORIENTATION_NAMES, Build, BuildCategory, BuildDraft, Status
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
    field_spec("image_urls", "any urls, comma separated"),
    field_spec("video_urls", "any urls, comma separated"),
    field_spec("world_download_urls", "any urls, comma separated"),
    field_spec("completion_time", "Any time format works"),
    field_spec("extra_user_info", "Anything a reader should know", display=FieldDisplay.PARAGRAPH),
    field_spec("server_ip", "play.example.com"),
    field_spec("coordinates", "x y z"),
    field_spec("command_to_get_to_build", "/warp door"),
)
"""Every entry must name a BuildEditPatch field; a test pins that."""


def _split_values(value: str) -> list[str]:
    """Split a user-facing comma-separated list while ignoring empty values."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _format_dimensions(value: tuple[int | None, ...]) -> str:
    """Format only dimensions that have actually been supplied."""
    if not any(item is not None for item in value):
        return ""
    return " x ".join("?" if item is None else str(item) for item in value)


def _submission_basics_form(build: BuildDraft, invocation: sd.Invocation) -> sl.forms.FormSpec:
    return sl.forms.FormSpec(
        invocation.t(L(t"Build basics")),
        (
            sl.forms.TextField(
                key="door_size",
                label=invocation.t(L(t"Door opening size")),
                placeholder=invocation.t(L(t"For example: 2x2")),
                default=_format_dimensions(build.door_dimensions),
                maximum=100,
            ),
            sl.forms.TextField(
                key="pattern",
                label=invocation.t(L(t"Pattern")),
                placeholder=invocation.t(L(t"For example: regular, full lamp")),
                default=", ".join(build.patterns),
                required=False,
                maximum=500,
            ),
            sl.forms.TextField(
                key="dimensions",
                label=invocation.t(L(t"Overall build size")),
                placeholder=invocation.t(L(t"Width x Height x Depth")),
                default=_format_dimensions(build.dimensions),
                required=False,
                maximum=100,
            ),
            sl.forms.TextField(
                key="versions",
                label=invocation.t(L(t"Supported versions")),
                placeholder=invocation.t(L(t"For example: 1.20.4+")),
                default=build.version_spec or "",
                required=False,
                maximum=200,
            ),
            sl.forms.TextField(
                key="creators",
                label=invocation.t(L(t"Creators")),
                placeholder=invocation.t(L(t"Minecraft names, comma separated")),
                default=", ".join(build.creators_ign),
                required=False,
                maximum=500,
            ),
        ),
    )


def _submission_details_form(build: BuildDraft, invocation: sd.Invocation) -> sl.forms.FormSpec:
    restrictions = (
        build.wiring_placement_restrictions
        + build.animated_restrictions
        + build.component_restrictions
        + build.miscellaneous_restrictions
    )
    return sl.forms.FormSpec(
        invocation.t(L(t"Links and optional details")),
        (
            sl.forms.TextField(
                key="restrictions",
                label=invocation.t(L(t"Restrictions")),
                placeholder=invocation.t(L(t"For example: Seamless, Observerless")),
                default=", ".join(restrictions),
                required=False,
                maximum=1000,
            ),
            sl.forms.TextField(
                key="image_urls",
                label=invocation.t(L(t"Images")),
                placeholder=invocation.t(L(t"Image links, comma separated")),
                default=", ".join(build.image_urls),
                required=False,
                maximum=4000,
            ),
            sl.forms.TextField(
                key="video_urls",
                label=invocation.t(L(t"Videos")),
                placeholder=invocation.t(L(t"Video links, comma separated")),
                default=", ".join(build.video_urls),
                required=False,
                maximum=4000,
            ),
            sl.forms.TextField(
                key="world_urls",
                label=invocation.t(L(t"World downloads")),
                placeholder=invocation.t(L(t"World download links, comma separated")),
                default=", ".join(build.world_download_urls),
                required=False,
                maximum=4000,
            ),
            sl.forms.TextAreaField(
                key="notes",
                label=invocation.t(L(t"Notes")),
                placeholder=invocation.t(L(t"Anything staff should know")),
                default=build.extra_info.get("user") or "",
                required=False,
                maximum=4000,
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """The persisted build and presentation shown by a completed submission screen."""

    build: Build
    node: sl.LayoutNode[sl.ComponentsV2Target]


class SubmissionScreen(sd.UserSessionScreen):
    """A submission draft that ends when it is submitted, cancelled, or times out."""

    session_name = "build-submission"
    admission = AdmissionSpec(collision=Reject(notice=L(t"You already have a submission draft open.")))
    timeout = 300

    validation_error: sl.TextLike | None = sl.state(None)
    submitting: bool = sl.state(default=False)
    cancelled: bool = sl.state(default=False)
    build: BuildDraft = sl.state(opaque=True)
    outcome: SubmissionOutcome | None = sl.state(None, opaque=True, persist=False)

    def __init__(
        self,
        build: BuildDraft,
        builds: BuildService,
        *,
        on_submit: Callable[[], Awaitable[SubmissionOutcome]],
    ) -> None:
        build.submission_status = Status.PENDING
        build.category = BuildCategory.DOOR
        build.patterns = build.patterns or ["Regular"]
        self.build = build
        self.builds = builds
        self.on_submit = on_submit

    @property
    def is_ready(self) -> bool:
        width, height, _depth = self.build.door_dimensions
        return self.build.door_orientation is not None and width is not None and height is not None

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self.cancelled:
            return (sl.status(L(t"Submission cancelled. Nothing was saved.")),)
        if self.outcome is not None:
            from squid.bot.submission.ui.controls import build_edit

            build_id = self.outcome.build.id
            assert build_id is not None, "a submitted build has a persistent id"
            return (
                sl.status(L(t"Submitted for review. Submission ID: {build_id}.")),
                self.outcome.node,
                sd.v2.section(
                    sd.v2.text(L(t"Staff can now review and vote on this build."), priority=-10),
                    accessory=sd.v2.routed_button(L(t"Edit"), build_edit.id(build_id=build_id)),
                ),
            )
        missing_door_type = self.build.door_orientation is None
        missing_opening_size = not self.build.door_width or not self.build.door_height
        guidance = self.validation_error
        if guidance is None and missing_door_type and missing_opening_size:
            guidance = L(t"Required before review: door type and door opening size.")
        elif guidance is None and missing_door_type:
            missing_field = "door type"
            guidance = L(t"Required before review: {missing_field}.")
        elif guidance is None and missing_opening_size:
            missing_field = "door opening size"
            guidance = L(t"Required before review: {missing_field}.")
        if guidance is None:
            guidance = L(t"Ready to submit. Optional details can be added later.")
        fields = (
            sl.field(L(t"Door type"), self.build.door_orientation or "—"),
            sl.field(L(t"Opening size"), _format_dimensions(self.build.door_dimensions) or "—"),
            sl.field(L(t"Pattern"), ", ".join(self.build.patterns)),
            sl.field(L(t"Build size"), _format_dimensions(self.build.dimensions) or "—"),
            sl.field(L(t"Versions"), self.build.version_spec or "—"),
            sl.field(L(t"Creators"), ", ".join(self.build.creators_ign) or "—"),
        )
        return (
            sl.section(
                sl.heading(L(t"Submit a build")),
                sl.truncate(sl.paragraph(guidance)),
                sl.fields(*fields),
                sl.note(L(t"Only the door type and opening size are required.")),
                accent=sl.palette.INHERIT if self.is_ready else DISCORD_YELLOW,
            ),
            sl.choices(
                *(sl.choice(L(value), key=value) for value in DOOR_ORIENTATION_NAMES),
                key="door_type",
                selection=sl.controlled(
                    (self.build.door_orientation,) if self.build.door_orientation is not None else (),
                    self._door_changed,
                ),
            ),
            sl.choices(
                sl.choice(
                    L(t"Directional"),
                    key="Directional",
                    description=L(t"May depend on the direction it faces"),
                ),
                sl.choice(
                    L(t"Locational"),
                    key="Locational",
                    description=L(t"May depend on its position in the world"),
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
                    L(t"Edit basics"),
                    self._edit_basics,
                    key="edit_basics",
                    emphasis=sl.semantic.Emphasis.STRONG,
                ),
                sl.action_control(
                    L(t"Add links & details"),
                    self._edit_details,
                    key="edit_details",
                ),
                sl.action_control(
                    L(t"Submit for review"),
                    self._submit,
                    key="submit",
                    tone=sl.Tone.SUCCESS,
                    available=not self.submitting,
                ),
                sl.action_control(L(t"Cancel"), self._cancel, key="cancel"),
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

    async def _edit_basics(self, event: sl.PressEvent) -> None:
        await event.present_form(
            _submission_basics_form(self.build, self.opening),
            key="submission-basics",
            on_submit=self._basics_submitted,
        )

    async def _basics_submitted(self, event: sl.SubmitEvent) -> None:
        values = event.values
        try:
            door_dimensions = parse_hallway_dimensions(cast(str, values["door_size"]))
            dimensions_text = cast(str, values["dimensions"])
            dimensions = parse_dimensions(dimensions_text) if dimensions_text.strip() else (None, None, None)
        except ValueError as error:
            error_text = str(error)
            await event.notice(L(t"Check the dimensions: {error_text}"))
            return
        if door_dimensions[0] is None or door_dimensions[1] is None:
            await event.notice(L(t"Enter at least a door width and height, such as `2x2`."))
            return
        self.build.door_dimensions = door_dimensions
        self.build.patterns = _split_values(cast(str, values["pattern"])) or ["Regular"]
        self.build.dimensions = dimensions
        self.build.version_spec = cast(str, values["versions"]).strip() or None
        self.build.creators_ign = _split_values(cast(str, values["creators"]))
        self.validation_error = None
        self.mutated(self.build)

    async def _edit_details(self, event: sl.PressEvent) -> None:
        await event.present_form(
            _submission_details_form(self.build, self.opening),
            key="submission-details",
            on_submit=self._details_submitted,
        )

    async def _details_submitted(self, event: sl.SubmitEvent) -> None:
        values = event.values
        image_urls = _split_values(cast(str, values["image_urls"]))
        video_urls = _split_values(cast(str, values["video_urls"]))
        world_urls = _split_values(cast(str, values["world_urls"]))
        invalid_urls = [
            url for url in (*image_urls, *video_urls, *world_urls) if not url.startswith(("https://", "http://"))
        ]
        if invalid_urls:
            await event.notice(L(t"Every link must start with `https://` or `http://`."))
            return
        await self.builds.classify_restrictions(self.build, _split_values(cast(str, values["restrictions"])))
        self.build.replace_links("image", image_urls)
        self.build.replace_links("video", video_urls)
        self.build.replace_links("world-download", world_urls)
        notes = cast(str, values["notes"]).strip()
        if notes:
            self.build.extra_info["user"] = notes
        else:
            self.build.extra_info.pop("user", None)
        self.validation_error = None
        self.mutated(self.build)

    async def _submit(self, event: sl.PressEvent) -> None:
        if self.outcome is not None:
            await event.notice(L(t"This build has already been submitted."))
            return
        if not self.is_ready:
            self.validation_error = L(t"Choose a door type and add an opening size such as `2x2` before submitting.")
            self.invalidate()
            return
        if self.submitting:
            await event.notice(L(t"This build is still being submitted. Give it a moment."))
            return
        self.submitting = True
        await event.acknowledge()
        try:
            self.outcome = await self.on_submit()
        except Exception:
            self.submitting = False
            self.validation_error = L(
                t"Submitting failed and nothing was saved. Press **Submit for review** to try again."
            )
            self.invalidate()
            raise
        self.submitting = False
        await event.finish()

    async def _cancel(self, event: sl.PressEvent) -> None:
        self.cancelled = True
        await event.finish()


def _edit_form(items: Sequence[BoundBuildField], page: int, invocation: sd.Invocation) -> sl.forms.FormSpec:
    fields: list[sl.forms.FormField[Any]] = []
    for item in items[5 * (page - 1) : 5 * page]:
        spec = item.spec
        field_type = sl.forms.TextAreaField if spec.display is FieldDisplay.PARAGRAPH else sl.forms.TextField
        fields.append(
            field_type(
                key=item.attribute,
                label=invocation.t(L(spec.label)),
                placeholder=spec.placeholder,
                default=item.current_text,
                required=spec.required,
                minimum=spec.minimum,
                maximum=spec.maximum,
            )
        )
    return sl.forms.FormSpec(invocation.t(L(t"Edit build, section {page}")), tuple(fields))


class BuildEditScreen(sd.UserSessionScreen):
    """A build editor that ends when saved, closed, replaced, or timed out."""

    session_name = "build-edit"
    timeout = 900
    follow_topics = True

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
    ) -> None:
        self._seed: tuple[Build, sl.LayoutNode[sl.ComponentsV2Target] | None] | None = (build, node)
        self._build_id = build.id
        self.builds = builds
        self._authorize = authorize
        self._render_build = render_build
        self._refresh_posts = refresh_posts
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
        if self.saved:
            return (
                sl.section(
                    sl.heading(L(t"Changes saved")),
                    sl.paragraph(L(t"The build card has been refreshed.")),
                ),
            )
        state = self.projection.status
        if self._seed is None and not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(L(t"Loading build.")),)
        page = self.page
        pages = self.max_pages
        validation_error = self.validation_error
        description = (
            L(t"Section {page} of {pages}. Filled dots have unsaved changes.")
            if not validation_error
            else L(t"Fix these values before review:\n{validation_error}")
        )
        controls: list[sl.semantic.ActionControl] = [
            sl.action_control(L(t"Edit this section"), self._open, key="open"),
            sl.action_control(L(t"Previous"), self._previous, key="previous", available=self.page != 1),
            sl.action_control(L(t"Next"), self._next, key="next", available=self.page != self.max_pages),
        ]
        if self.confirming:
            controls.extend(
                (
                    sl.action_control(
                        L(t"Apply changes"),
                        self._apply,
                        key="apply",
                        tone=sl.Tone.SUCCESS,
                    ),
                    sl.action_control(L(t"Back"), self._unconfirm, key="unconfirm"),
                )
            )
        else:
            controls.append(
                sl.action_control(
                    L(t"Review changes"),
                    self._review,
                    key="review",
                    tone=sl.Tone.SUCCESS,
                )
            )
        controls.append(sl.action_control(L(t"Close"), self._close, key="close"))
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.section(
                sl.heading(L(t"Edit build")),
                sl.truncate(sl.paragraph(description)),
                sl.fields(sl.field(L(t"Fields in this section"), self.summary_text())),
                accent=DISCORD_YELLOW if self.validation_error else sl.palette.INHERIT,
            )
        ]
        if (node := self._current()[1]) is not None:
            nodes.append(node)
        nodes.append(
            sl.action_controls(*controls, key="build-edit-actions", display=sl.semantic.ControlDisplay.INDIVIDUAL)
        )
        return tuple(nodes)

    def summary_text(self) -> str:
        page_items = self.items[5 * (self.page - 1) : 5 * self.page]
        return "\n".join(f"{'●' if item.modified else '○'} {item.summary}" for item in page_items)

    async def _open(self, event: sl.PressEvent) -> None:
        if await self._may_event(event):
            await event.present_form(
                _edit_form(self.items, self.page, self.opening),
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
            await event.notice(L(t"Fix these values before review:\n{error_text}"))
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
            self.validation_error = L(t"No changes to review yet.")
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
            async with self.builds.edit(build.id, patch) as edit:
                build = await edit.commit()
            edited_build_id = build.id
        self.saved = True
        self.confirming = False
        self._replace(build, await self._render_build(build))
        await event.finish()
        if edited_build_id is not None:
            await self._refresh_posts(edited_build_id)

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()

    async def _may_event(self, event: sl.ActionEvent) -> bool:
        if not await self.may_edit():
            await event.notice(L(t"Only the pending build's submitter or a trusted staff member can edit it."))
            return False
        return True
