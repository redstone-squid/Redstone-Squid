"""Semantic submission and build-edit workspaces."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, cast, override

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.input import parse_web_urls
from squid.bot.submission.ui.fields import (
    BoundBuildField,
    BuildFieldSpec,
    FieldDisplay,
    field_spec,
)
from squid.bot.ui import DISCORD_YELLOW, tr
from squid.bot.utils.sentinel import DEFAULT, DefaultType
from squid.builds.application import BuildEditPatch, BuildService
from squid.builds.domain import Build, BuildCategory, DoorBuild
from squid.builds.errors import BuildRevisionMismatchError
from squid.topics import resource_topic

_DOOR_ONLY = frozenset({BuildCategory.DOOR})


def _door(build: Build) -> DoorBuild:
    if not isinstance(build, DoorBuild):
        message = "door-only field bound to a non-door build"
        raise TypeError(message)
    return build


def _server_info_value(build: Build, key: str) -> str | None:
    return cast(str | None, build.extra_info.get("server_info", {}).get(key))


EDIT_FIELDS: tuple[BuildFieldSpec[Any], ...] = (
    field_spec(
        "dimensions",
        tuple[int | None, int | None, int | None],
        "Width x Height x Depth",
        reader=lambda build: build.dimensions,
        patch=lambda value: BuildEditPatch(dimensions=value),
        required=True,
    ),
    field_spec(
        "door_dimensions",
        tuple[int | None, int | None, int | None],
        "2x2",
        reader=lambda build: _door(build).door_dimensions,
        patch=lambda value: BuildEditPatch(door_dimensions=value),
        required=True,
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "version_spec",
        str | None,
        "1.16 - 1.17.3",
        reader=lambda build: build.version_spec,
        patch=lambda value: BuildEditPatch(version_spec=value),
    ),
    field_spec(
        "door_type",
        list[str],
        "Full lamp, Funnel",
        reader=lambda build: list(build.patterns),
        patch=lambda value: BuildEditPatch(door_type=value),
    ),
    field_spec(
        "door_orientation_type",
        str | None,
        "Door, Trapdoor, Skydoor",
        reader=lambda build: _door(build).orientation,
        patch=lambda value: BuildEditPatch(door_orientation_type=value),
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "wiring_placement_restrictions",
        list[str],
        "Seamless, Full Flush",
        reader=lambda build: list(build.wiring_placement_restrictions),
        patch=lambda value: BuildEditPatch(wiring_placement_restrictions=value),
    ),
    field_spec(
        "animated_restrictions",
        list[str],
        "Symmetrical, Full Sync",
        reader=lambda build: list(build.animated_restrictions),
        patch=lambda value: BuildEditPatch(animated_restrictions=value),
    ),
    field_spec(
        "component_restrictions",
        list[str],
        "Observerless",
        reader=lambda build: list(build.component_restrictions),
        patch=lambda value: BuildEditPatch(component_restrictions=value),
    ),
    field_spec(
        "miscellaneous_restrictions",
        list[str],
        "Directional, Locational",
        reader=lambda build: list(build.miscellaneous_restrictions),
        patch=lambda value: BuildEditPatch(miscellaneous_restrictions=value),
    ),
    field_spec(
        "normal_closing_time",
        int | None,
        "in gameticks",
        reader=lambda build: _door(build).normal_closing_time,
        patch=lambda value: BuildEditPatch(normal_closing_time=value),
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "normal_opening_time",
        int | None,
        "in gameticks",
        reader=lambda build: _door(build).normal_opening_time,
        patch=lambda value: BuildEditPatch(normal_opening_time=value),
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "creators_ign",
        list[str],
        "Me, My Dog",
        reader=lambda build: list(build.creators_ign),
        patch=lambda value: BuildEditPatch(creators_ign=value),
    ),
    field_spec(
        "image_urls",
        list[str],
        "any urls, comma separated",
        reader=lambda build: list(build.image_urls),
        patch=lambda value: BuildEditPatch(image_urls=value),
        parser=parse_web_urls,
    ),
    field_spec(
        "video_urls",
        list[str],
        "any urls, comma separated",
        reader=lambda build: list(build.video_urls),
        patch=lambda value: BuildEditPatch(video_urls=value),
        parser=parse_web_urls,
    ),
    field_spec(
        "world_download_urls",
        list[str],
        "any urls, comma separated",
        reader=lambda build: list(build.world_download_urls),
        patch=lambda value: BuildEditPatch(world_download_urls=value),
        parser=parse_web_urls,
    ),
    field_spec(
        "completion_time",
        str | None,
        "Any time format works",
        reader=lambda build: build.completion_time,
        patch=lambda value: BuildEditPatch(completion_time=value),
    ),
    field_spec(
        "extra_user_info",
        str | None,
        "Anything a reader should know",
        reader=lambda build: build.description,
        patch=lambda value: BuildEditPatch(extra_user_info=value),
        display=FieldDisplay.PARAGRAPH,
    ),
    field_spec(
        "server_ip",
        str | None,
        "play.example.com",
        reader=lambda build: _server_info_value(build, "server_ip"),
        patch=lambda value: BuildEditPatch(server_ip=value),
    ),
    field_spec(
        "coordinates",
        str | None,
        "x y z",
        reader=lambda build: _server_info_value(build, "coordinates"),
        patch=lambda value: BuildEditPatch(coordinates=value),
    ),
    field_spec(
        "command_to_get_to_build",
        str | None,
        "/warp door",
        reader=lambda build: _server_info_value(build, "command_to_build"),
        patch=lambda value: BuildEditPatch(command_to_get_to_build=value),
    ),
)
"""Every entry owns its typed read and patch operations; UI keys carry no mutation authority."""


def _edit_form(items: Sequence[BoundBuildField[Any]], page: int) -> sl.forms.FormSpec:
    page_items = items[5 * (page - 1) : 5 * page]
    fields: list[sl.forms.FormField[Any]] = []
    for item in page_items:
        spec = item.spec
        field_type = sl.forms.TextAreaField if spec.display is FieldDisplay.PARAGRAPH else sl.forms.TextField
        fields.append(
            field_type(
                key=item.key,
                label=tr(spec.label),
                placeholder=tr(spec.placeholder),
                default=item.current_text,
                required=spec.required,
                minimum=spec.minimum,
                maximum=spec.maximum,
            )
        )

    def validate(values: Mapping[str, object]) -> tuple[sl.forms.FormIssue, ...]:
        errors: list[sl.forms.FormIssue] = []
        for item in page_items:
            try:
                item.spec.parser(cast(str, values[item.key]))
            except ValueError as error:
                errors.append(sl.forms.FieldError(item.key, str(error) or tr(t"Invalid value")))
        return tuple(errors)

    return sl.forms.FormSpec(tr(t"Edit build, section {page}"), tuple(fields), validator=validate)


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
        items: Sequence[BoundBuildField[Any]] | DefaultType = DEFAULT,
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

    def stage(self, key: str, text: str) -> bool:
        for item in self.items:
            if item.key == key:
                item.stage(text)
                previous = self.validation_error if isinstance(self.validation_error, str) else None
                self.validation_error = "\n".join(error for error in (previous, item.validation_error) if error) or None
                return True
        return False

    async def may_edit(self) -> bool:
        """Recheck whether the actor may currently edit this build."""
        return await self._authorize()

    @override
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
            item.stage(cast(str, event.values[item.key]))
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
        patch = BuildEditPatch.combine(item.to_patch() for item in changed)
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
