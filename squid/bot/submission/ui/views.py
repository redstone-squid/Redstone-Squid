"""Semantic submission and build-edit workspaces."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

import anyio
import discord
from whenever import Instant

import squid_discord as sd
import squid_layouts as sl
from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.ui.components import BuildField, get_text_input
from squid.bot.ui import DISCORD_BLUE, DISCORD_YELLOW, create_mount, error_layout, respond_presentation
from squid.bot.utils.permissions import allows
from squid.bot.utils.sentinel import DEFAULT, DefaultType
from squid.builds.application import BuildEditPatch, BuildService
from squid.builds.domain import DOOR_ORIENTATION_NAMES, Build, BuildCategory, BuildDraft, Status
from squid.core.i18n import _
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid.topics import resource_topic
from squid_discord import SessionKey


@dataclass(frozen=True, slots=True)
class EditFieldSpec:
    """Describe one portable build-edit field."""

    attribute: str
    placeholder: str
    required: bool = False
    categories: frozenset[BuildCategory] | None = None

    def applies_to(self, build: Build) -> bool:
        return self.categories is None or build.category in self.categories


_DOOR_ONLY = frozenset({BuildCategory.DOOR})

EDIT_FIELDS: tuple[EditFieldSpec, ...] = (
    EditFieldSpec("dimensions", "Width x Height x Depth", required=True),
    EditFieldSpec("door_dimensions", "2x2", required=True, categories=_DOOR_ONLY),
    EditFieldSpec("version_spec", "1.16 - 1.17.3"),
    EditFieldSpec("door_type", "Full lamp, Funnel"),
    EditFieldSpec("door_orientation_type", "Door, Trapdoor, Skydoor", categories=_DOOR_ONLY),
    EditFieldSpec("wiring_placement_restrictions", "Seamless, Full Flush"),
    EditFieldSpec("animated_restrictions", "Symmetrical, Full Sync"),
    EditFieldSpec("component_restrictions", "Observerless"),
    EditFieldSpec("miscellaneous_restrictions", "Directional, Locational"),
    EditFieldSpec("normal_closing_time", "in gameticks", categories=_DOOR_ONLY),
    EditFieldSpec("normal_opening_time", "in gameticks", categories=_DOOR_ONLY),
    EditFieldSpec("creators_ign", "Me, My Dog"),
    EditFieldSpec("image_urls", "any urls, comma separated"),
    EditFieldSpec("video_urls", "any urls, comma separated"),
    EditFieldSpec("world_download_urls", "any urls, comma separated"),
    EditFieldSpec("completion_time", "Any time format works"),
    EditFieldSpec("extra_user_info", "Anything a reader should know"),
    EditFieldSpec("server_ip", "play.example.com"),
    EditFieldSpec("coordinates", "x y z"),
    EditFieldSpec("command_to_get_to_build", "/warp door"),
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


def _submission_basics_form(build: BuildDraft, locale: str | None) -> sl.forms.FormSpec:
    return sl.forms.FormSpec(
        t(locale, _("Build basics")),
        (
            sl.forms.TextField(
                key="door_size",
                label=t(locale, _("Door opening size")),
                placeholder=t(locale, _("For example: 2x2")),
                default=_format_dimensions(build.door_dimensions),
                maximum=100,
            ),
            sl.forms.TextField(
                key="pattern",
                label=t(locale, _("Pattern")),
                placeholder=t(locale, _("For example: regular, full lamp")),
                default=", ".join(build.patterns),
                required=False,
                maximum=500,
            ),
            sl.forms.TextField(
                key="dimensions",
                label=t(locale, _("Overall build size")),
                placeholder=t(locale, _("Width x Height x Depth")),
                default=_format_dimensions(build.dimensions),
                required=False,
                maximum=100,
            ),
            sl.forms.TextField(
                key="versions",
                label=t(locale, _("Supported versions")),
                placeholder=t(locale, _("For example: 1.20.4+")),
                default=build.version_spec or "",
                required=False,
                maximum=200,
            ),
            sl.forms.TextField(
                key="creators",
                label=t(locale, _("Creators")),
                placeholder=t(locale, _("Minecraft names, comma separated")),
                default=", ".join(build.creators_ign),
                required=False,
                maximum=500,
            ),
        ),
    )


def _submission_details_form(build: BuildDraft, locale: str | None) -> sl.forms.FormSpec:
    restrictions = (
        build.wiring_placement_restrictions
        + build.animated_restrictions
        + build.component_restrictions
        + build.miscellaneous_restrictions
    )
    return sl.forms.FormSpec(
        t(locale, _("Links and optional details")),
        (
            sl.forms.TextField(
                key="restrictions",
                label=t(locale, _("Restrictions")),
                placeholder=t(locale, _("For example: Seamless, Observerless")),
                default=", ".join(restrictions),
                required=False,
                maximum=1000,
            ),
            sl.forms.TextField(
                key="image_urls",
                label=t(locale, _("Images")),
                placeholder=t(locale, _("Image links, comma separated")),
                default=", ".join(build.image_urls),
                required=False,
                maximum=4000,
            ),
            sl.forms.TextField(
                key="video_urls",
                label=t(locale, _("Videos")),
                placeholder=t(locale, _("Video links, comma separated")),
                default=", ".join(build.video_urls),
                required=False,
                maximum=4000,
            ),
            sl.forms.TextField(
                key="world_urls",
                label=t(locale, _("World downloads")),
                placeholder=t(locale, _("World download links, comma separated")),
                default=", ".join(build.world_download_urls),
                required=False,
                maximum=4000,
            ),
            sl.forms.TextAreaField(
                key="notes",
                label=t(locale, _("Notes")),
                placeholder=t(locale, _("Anything staff should know")),
                default=build.extra_info.get("user") or "",
                required=False,
                maximum=4000,
            ),
        ),
    )


class SubmissionFormComponent(sl.Component):
    """A semantic, resumable submission workspace."""

    value: bool | None = sl.state(None)
    validation_error: str | None = sl.state(None)
    submitting: bool = sl.state(default=False)
    closed: bool = sl.state(default=False)
    build: BuildDraft = sl.state(opaque=True)

    def __init__(
        self,
        build: BuildDraft,
        builds: BuildService,
        *,
        author_id: int | None = None,
        locale: str | None = None,
        timeout: float = 300,
        on_submit: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        build.submission_status = Status.PENDING
        build.category = BuildCategory.DOOR
        build.patterns = build.patterns or ["Regular"]
        self.build = build
        self.builds = builds
        self.author_id = author_id
        self.locale = locale
        self._timeout = timeout
        self.on_submit = on_submit
        self._done = anyio.Event()
        self._mount: sd.Mount | None = None

    @property
    def is_ready(self) -> bool:
        width, height, _depth = self.build.door_dimensions
        return self.build.door_orientation is not None and width is not None and height is not None

    def render(self) -> tuple[sl.LayoutNode, ...]:
        if self.closed:
            return (sl.section(sl.heading(t(self.locale, _("Submission closed"))), accent=DISCORD_BLUE),)
        missing = []
        if self.build.door_orientation is None:
            missing.append(t(self.locale, _("door type")))
        if not self.build.door_width or not self.build.door_height:
            missing.append(t(self.locale, _("door opening size")))
        guidance = self.validation_error
        if guidance is None and missing:
            guidance = t(self.locale, _("Required before review: {fields}."), fields=", ".join(missing))
        if guidance is None:
            guidance = t(self.locale, _("Ready to submit. Optional details can be added later."))
        fields = (
            sl.field(t(self.locale, _("Door type")), self.build.door_orientation or "—"),
            sl.field(t(self.locale, _("Opening size")), _format_dimensions(self.build.door_dimensions) or "—"),
            sl.field(t(self.locale, _("Pattern")), ", ".join(self.build.patterns)),
            sl.field(t(self.locale, _("Build size")), _format_dimensions(self.build.dimensions) or "—"),
            sl.field(t(self.locale, _("Versions")), self.build.version_spec or "—"),
            sl.field(t(self.locale, _("Creators")), ", ".join(self.build.creators_ign) or "—"),
        )
        return (
            sl.section(
                sl.heading(t(self.locale, _("Submit a build"))),
                sl.truncate(sl.paragraph(guidance)),
                sl.fields(*fields),
                sl.note(t(self.locale, _("Only the door type and opening size are required."))),
                accent=DISCORD_BLUE if self.is_ready else DISCORD_YELLOW,
            ),
            sl.semantic.Choices(
                key="door_type",
                choices=tuple(sl.semantic.Choice(value, t(self.locale, _(value))) for value in DOOR_ORIENTATION_NAMES),
                selection=sl.controlled(
                    (self.build.door_orientation,) if self.build.door_orientation is not None else (),
                    self._door_changed,
                ),
            ),
            sl.semantic.Choices(
                key="location",
                choices=(
                    sl.semantic.Choice(
                        "Directional",
                        t(self.locale, _("Directional")),
                        t(self.locale, _("May depend on the direction it faces")),
                    ),
                    sl.semantic.Choice(
                        "Locational",
                        t(self.locale, _("Locational")),
                        t(self.locale, _("May depend on its position in the world")),
                    ),
                ),
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
            sl.primitives.Row(
                (
                    sl.primitives.Button(
                        t(self.locale, _("Edit basics")),
                        self._edit_basics,
                        "edit_basics",
                        style=sl.primitives.ActionStyle.PRIMARY,
                    ),
                    sl.primitives.Button(
                        t(self.locale, _("Add links & details")),
                        self._edit_details,
                        "edit_details",
                    ),
                    sl.primitives.Button(
                        t(self.locale, _("Submit for review")),
                        self._submit,
                        "submit",
                        style=sl.primitives.ActionStyle.SUCCESS,
                        disabled=self.submitting,
                    ),
                    sl.primitives.Button(t(self.locale, _("Cancel")), self._cancel, "cancel"),
                )
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
            _submission_basics_form(self.build, self.locale),
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
            await event.notice(t(self.locale, _("Check the dimensions: {error}"), error=str(error)))
            return
        if door_dimensions[0] is None or door_dimensions[1] is None:
            await event.notice(t(self.locale, _("Enter at least a door width and height, such as `2x2`.")))
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
            _submission_details_form(self.build, self.locale),
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
            await event.notice(t(self.locale, _("Every link must start with `https://` or `http://`.")))
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
        if not self.is_ready:
            self.validation_error = t(
                self.locale,
                _("Choose a door type and add an opening size such as `2x2` before submitting."),
            )
            self.invalidate()
            return
        if self.submitting:
            await event.notice(t(self.locale, _("This build is still being submitted. Give it a moment.")))
            return
        self.submitting = True
        await event.acknowledge()
        try:
            if self.on_submit is not None:
                await self.on_submit()
        except Exception:
            self.submitting = False
            self.validation_error = t(
                self.locale,
                _("Submitting failed and nothing was saved. Press **Submit for review** to try again."),
            )
            self.invalidate()
            raise
        self.submitting = False
        self.value = True
        self.closed = True
        self._done.set()
        await event.finish()

    async def _cancel(self, event: sl.PressEvent) -> None:
        self.value = False
        self.closed = True
        self._done.set()
        await event.finish()

    async def refresh(self, interaction: discord.Interaction[Any]) -> None:
        """Flush a changed draft when called by an external integration."""
        self.validation_error = None
        self.mutated(self.build)
        if self._mount is not None:
            await self._mount.flush(interaction)

    async def wait(self) -> bool | None:
        with anyio.move_on_after(self._timeout) as scope:
            await self._done.wait()
        return None if scope.cancel_called else self.value

    def mount(self, *, source: sd.host.HostSource) -> sd.Mount:
        self._mount = create_mount(
            self,
            source=source,
            access=(sd.Owner(self.author_id) if self.author_id is not None else sd.Everyone()),
            locale=self.locale,
            timeout=self._timeout,
        )
        return self._mount


def _edit_form(items: Sequence[BuildField[Any]], page: int, locale: str | None) -> sl.forms.FormSpec:
    fields: list[sl.forms.FormField[Any]] = []
    for item in items[5 * (page - 1) : 5 * page]:
        field_type = sl.forms.TextAreaField if item.style is discord.TextStyle.paragraph else sl.forms.TextField
        fields.append(
            field_type(
                key=item.attribute,
                label=t(locale, _(item.display_label)),
                placeholder=item.placeholder,
                default=item.current_string_value,
                required=item.required,
                maximum=item.max_length,
            )
        )
    return sl.forms.FormSpec(t(locale, _("Edit build, section {page}"), page=page), tuple(fields))


class BuildEditComponent(sl.Component):
    """A mounted build editor with semantic pagination, forms, and confirmation."""

    page: int = sl.state(1)
    confirming: bool = sl.state(default=False)
    saved: bool = sl.state(default=False)
    validation_error: str | None = sl.state(None)
    locale: str | None = sl.state(None, persist=False)

    def __init__(
        self,
        build: Build,
        builds: BuildService,
        items: Sequence[BuildField[Any]] | DefaultType = DEFAULT,
        *,
        locale: str | None = None,
        timeout: float = 300,
        node: sl.LayoutNode | None = None,
    ) -> None:
        self._seed: tuple[Build, sl.LayoutNode | None] | None = (build, node)
        self._build_id = build.id
        self._refresh: Callable[[int], Awaitable[tuple[Build, sl.LayoutNode] | None]] | None = None
        self.builds = builds
        self.locale = locale
        self._timeout = timeout
        self.expiry_time = Instant.now().add(seconds=timeout)
        if items is DEFAULT:
            items = [
                get_text_input(build, field.attribute, placeholder=field.placeholder, required=field.required)
                for field in EDIT_FIELDS
                if field.applies_to(build)
            ]
        self.items = tuple(items)
        self._mount: sd.Mount | None = None

    @sl.resource(pending=sl.resources.PendingPolicy.ATOMIC)
    async def projection(self) -> tuple[Build, sl.LayoutNode | None]:
        """Load the edited build and keep its preview current with the build topic."""
        if self._build_id is not None:
            sl.runtime.watch(resource_topic("build", str(self._build_id)))
        seed, self._seed = self._seed, None
        if seed is not None:
            return seed
        if self._refresh is None or self._build_id is None:
            message = "this editor has no way to reload itself"
            raise sl.resources.ResourceNotReadyError(message)
        latest = await self._refresh(self._build_id)
        if latest is None:
            message = f"build {self._build_id} no longer exists"
            raise LookupError(message)
        return latest

    def _current(self) -> tuple[Build, sl.LayoutNode | None]:
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

    def _replace(self, build: Build, node: sl.LayoutNode | None) -> None:
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
                self.validation_error = (
                    "\n".join(error for error in (self.validation_error, item.validation_error) if error) or None
                )
                return True
        return False

    async def can_edit(self, interaction: discord.Interaction[Any]) -> bool:
        actor_account_id = await interaction.client.account_ids.resolve(
            interaction.client.services.accounts,
            interaction.user.id,
        )
        if (
            self.build.submission_status is Status.PENDING
            and actor_account_id is not None
            and self.build.submitter_account_id == actor_account_id
        ):
            return True
        return await allows(interaction, BUILD_SUBMISSION_EDIT)

    def render(self) -> tuple[sl.LayoutNode, ...]:
        if self.saved:
            return (
                sl.section(
                    sl.heading(t(self.locale, _("Changes saved"))),
                    sl.paragraph(t(self.locale, _("The build card has been refreshed."))),
                    accent=DISCORD_BLUE,
                ),
            )
        state = self.projection.status
        if self._seed is None and not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(t(self.locale, _("Loading build."))),)
        description = (
            t(
                self.locale,
                _("Section {page} of {pages}. Filled dots have unsaved changes."),
                page=self.page,
                pages=self.max_pages,
            )
            if not self.validation_error
            else t(self.locale, _("Fix these values before review:\n{errors}"), errors=self.validation_error)
        )
        controls: list[sl.primitives.Button] = [
            sl.primitives.Button(t(self.locale, _("Edit this section")), self._open, "open"),
            sl.primitives.Button(t(self.locale, _("Previous")), self._previous, "previous", disabled=self.page == 1),
            sl.primitives.Button(t(self.locale, _("Next")), self._next, "next", disabled=self.page == self.max_pages),
        ]
        if self.confirming:
            controls.extend(
                (
                    sl.primitives.Button(
                        t(self.locale, _("Apply changes")),
                        self._apply,
                        "apply",
                        style=sl.primitives.ActionStyle.SUCCESS,
                    ),
                    sl.primitives.Button(t(self.locale, _("Back")), self._unconfirm, "unconfirm"),
                )
            )
        else:
            controls.append(
                sl.primitives.Button(
                    t(self.locale, _("Review changes")),
                    self._review,
                    "review",
                    style=sl.primitives.ActionStyle.SUCCESS,
                )
            )
        controls.append(sl.primitives.Button(t(self.locale, _("Close")), self._close, "close"))
        nodes: list[sl.LayoutNode] = [
            sl.section(
                sl.heading(t(self.locale, _("Edit build"))),
                sl.truncate(sl.paragraph(description)),
                sl.fields(sl.field(t(self.locale, _("Fields in this section")), self.summary_text())),
                accent=DISCORD_YELLOW if self.validation_error else DISCORD_BLUE,
            )
        ]
        if (node := self._current()[1]) is not None:
            nodes.append(node)
        nodes.append(sl.primitives.ActionGroup(tuple(controls)))
        return tuple(nodes)

    def summary_text(self) -> str:
        page_items = self.items[5 * (self.page - 1) : 5 * self.page]
        return "\n".join(f"{'●' if item.modified else '○'} {item.summary}" for item in page_items)

    async def _open(self, event: sl.PressEvent) -> None:
        if await self._may_event(event):
            await event.present_form(_edit_form(self.items, self.page, self.locale), key="edit", on_submit=self._edited)

    async def _edited(self, event: sl.SubmitEvent) -> None:
        errors: list[str] = []
        for item in self.items[5 * (self.page - 1) : 5 * self.page]:
            item.stage(cast(str, event.values[item.attribute]))
            if item.validation_error:
                errors.append(f"**{item.display_label}:** {item.validation_error}")
        self.validation_error = "\n".join(errors) or None
        if errors:
            await event.notice(t(self.locale, _("Fix these values before review:\n{errors}"), errors="\n".join(errors)))
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
            self.validation_error = t(self.locale, _("No changes to review yet."))
            return
        self.confirming = True

    async def _unconfirm(self, event: sl.PressEvent) -> None:
        self.confirming = False

    async def _apply(self, event: sl.PressEvent) -> None:
        if not await self._may_event(event):
            return
        interaction = sd.native(event)
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
        self._replace(build, await interaction.client.for_build(build).render_node())
        await event.finish()
        if edited_build_id is not None:
            await interaction.client.refresh_posts("build", str(edited_build_id))

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()

    async def _may_event(self, event: sl.ActionEvent) -> bool:
        interaction = sd.native(event)
        if Instant.now() > self.expiry_time:
            await event.notice(t(self.locale, _("This edit session expired. Reopen the build to start again.")))
            return False
        if not await self.can_edit(interaction):
            await event.notice(
                t(self.locale, _("Only the pending build's submitter or a trusted staff member can edit it."))
            )
            return False
        return True

    async def update(self, interaction: discord.Interaction[Any]) -> None:
        self.validation_error = None
        build = self.build
        self._replace(build, await interaction.client.for_build(build).render_node())
        self.invalidate()
        if self._mount is not None:
            await self._mount.flush(interaction)

    async def send(
        self,
        interaction: discord.Interaction[Any],
        ephemeral: bool = True,
        *,
        parent: sd.Mount | None = None,
    ) -> None:
        """Open an editor for this build, replacing this user's previous one."""
        self.locale = await resolve_locale(interaction, interaction.client.services.settings)
        if not await self.can_edit(interaction):
            message = t(
                self.locale,
                _("Only the pending build's submitter or a trusted staff member can edit it."),
            )
            await respond_presentation(interaction, error_layout(t(self.locale, _("Cannot edit this build")), message))
            return
        client = interaction.client
        build, node = self._current()
        if node is None:
            render_node = getattr(client.for_build(build), "render_node", None)
            node = (
                await render_node()
                if render_node is not None
                else sl.status(t(self.locale, _("Build preview unavailable.")))
            )
            self._seed = (build, node)

        async def refresh(build_id: int) -> tuple[Build, sl.LayoutNode] | None:
            latest = await self.builds.get(build_id)
            if latest is None:
                return None
            return latest, await client.for_build(latest).render_node()

        self._refresh = refresh
        mount = self.mount(interaction.user.id, source=interaction, reactor=client.layout_reactor)
        destination = sd.respond_to(interaction, ephemeral=ephemeral, wait=True)
        parent_session = None if parent is None else interaction.client.mounts.session_for(parent)
        if parent_session is None:
            await interaction.client.mounts.open(
                mount,
                destination,
                key=SessionKey.custom("build-edit", (interaction.user.id, self.build.id)),
                actor_id=interaction.user.id,
            )
        else:
            await parent_session.attach(mount, destination, actor_id=interaction.user.id, parent=parent)

    def mount(
        self, user_id: int, *, source: sd.host.HostSource, reactor: sd.Reactor | None = None
    ) -> sd.Mount:
        self._mount = create_mount(
            self,
            source=source,
            access=sd.Owner(user_id),
            locale=self.locale,
            timeout=self._timeout,
            reactor=reactor,
        )
        return self._mount
