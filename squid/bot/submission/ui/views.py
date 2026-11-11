"""Stage and review patches to existing builds in a Discord editor.

BuildEditScreen keeps field changes local until the user saves one combined patch.
New submissions use DraftEditorScreen in drafts.py instead: its answers are saved
as a retained submission draft after each context and later finalized into a build.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, cast, override

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.input import parse_web_urls
from squid.bot.submission.ui import controls as build_controls
from squid.bot.submission.ui.fields import (
    DIMENSIONS_FORMAT,
    OPTIONAL_INT_FORMAT,
    OPTIONAL_TEXT_FORMAT,
    STRING_LIST_FORMAT,
    BoundBuildField,
    BuildFieldSpec,
    FieldDisplay,
    field_spec,
)
from squid.bot.ui import DISCORD_YELLOW, tr
from squid.bot.utils.permissions import subject_for_interaction
from squid.builds.application import BuildEditor, BuildEditPatch, BuildService
from squid.builds.domain import BuildCategory
from squid.builds.errors import BuildRevisionMismatchError
from squid.builds.models import Build, DoorBuild
from squid.core.errors import AuthorizationError
from squid.core.sentinel import DEFAULT
from squid_reactivity import TopicHandle
from squid_ui.text import Message

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
        DIMENSIONS_FORMAT,
        tr(t"""
            bot-build-field-dimensions =
                .label = Dimensions
                .placeholder = Width x Height x Depth
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-dimensions =
                .label = Dimensions
                .placeholder = Width x Height x Depth
            """).attribute("label"),
        reader=lambda build: build.dimensions,
        patch=lambda value: BuildEditPatch(dimensions=value),
        required=True,
    ),
    field_spec(
        "door_dimensions",
        DIMENSIONS_FORMAT,
        tr(t"""
            bot-build-field-door-dimensions =
                .label = Door Dimensions
                .placeholder = 2x2
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-door-dimensions =
                .label = Door Dimensions
                .placeholder = 2x2
            """).attribute("label"),
        reader=lambda build: _door(build).door_dimensions,
        patch=lambda value: BuildEditPatch(door_dimensions=value),
        required=True,
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "version_spec",
        OPTIONAL_TEXT_FORMAT,
        tr(t"""
            bot-build-field-version-spec =
                .label = Version Spec
                .placeholder = 1.16 - 1.17.3
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-version-spec =
                .label = Version Spec
                .placeholder = 1.16 - 1.17.3
            """).attribute("label"),
        reader=lambda build: build.version_spec,
        patch=lambda value: BuildEditPatch(version_spec=value),
    ),
    field_spec(
        "door_type",
        STRING_LIST_FORMAT,
        tr(t"""
            bot-build-field-door-type =
                .label = Door Type
                .placeholder = Full lamp, Funnel
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-door-type =
                .label = Door Type
                .placeholder = Full lamp, Funnel
            """).attribute("label"),
        reader=lambda build: list(build.patterns),
        patch=lambda value: BuildEditPatch(door_type=value),
    ),
    field_spec(
        "door_orientation_type",
        OPTIONAL_TEXT_FORMAT,
        tr(t"""
            bot-build-field-door-orientation-type =
                .label = Door Orientation Type
                .placeholder = Door, Trapdoor, Skydoor
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-door-orientation-type =
                .label = Door Orientation Type
                .placeholder = Door, Trapdoor, Skydoor
            """).attribute("label"),
        reader=lambda build: _door(build).orientation,
        patch=lambda value: BuildEditPatch(door_orientation_type=value),
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "wiring_placement_restrictions",
        STRING_LIST_FORMAT,
        tr(t"""
            bot-build-field-wiring-placement-restrictions =
                .label = Wiring Placement Restrictions
                .placeholder = Seamless, Full Flush
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-wiring-placement-restrictions =
                .label = Wiring Placement Restrictions
                .placeholder = Seamless, Full Flush
            """).attribute("label"),
        reader=lambda build: list(build.wiring_placement_restrictions),
        patch=lambda value: BuildEditPatch(wiring_placement_restrictions=value),
    ),
    field_spec(
        "animated_restrictions",
        STRING_LIST_FORMAT,
        tr(t"""
            bot-build-field-animated-restrictions =
                .label = Animated Restrictions
                .placeholder = Symmetrical, Full Sync
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-animated-restrictions =
                .label = Animated Restrictions
                .placeholder = Symmetrical, Full Sync
            """).attribute("label"),
        reader=lambda build: list(build.animated_restrictions),
        patch=lambda value: BuildEditPatch(animated_restrictions=value),
    ),
    field_spec(
        "component_restrictions",
        STRING_LIST_FORMAT,
        tr(t"""
            bot-build-field-component-restrictions =
                .label = Component Restrictions
                .placeholder = Observerless
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-component-restrictions =
                .label = Component Restrictions
                .placeholder = Observerless
            """).attribute("label"),
        reader=lambda build: list(build.component_restrictions),
        patch=lambda value: BuildEditPatch(component_restrictions=value),
    ),
    field_spec(
        "miscellaneous_restrictions",
        STRING_LIST_FORMAT,
        tr(t"""
            bot-build-field-miscellaneous-restrictions =
                .label = Miscellaneous Restrictions
                .placeholder = Directional, Locational
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-miscellaneous-restrictions =
                .label = Miscellaneous Restrictions
                .placeholder = Directional, Locational
            """).attribute("label"),
        reader=lambda build: list(build.miscellaneous_restrictions),
        patch=lambda value: BuildEditPatch(miscellaneous_restrictions=value),
    ),
    field_spec(
        "normal_closing_time",
        OPTIONAL_INT_FORMAT,
        tr(t"""
            bot-build-field-normal-closing-time =
                .label = Normal Closing Time
                .placeholder = in gameticks
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-normal-closing-time =
                .label = Normal Closing Time
                .placeholder = in gameticks
            """).attribute("label"),
        reader=lambda build: _door(build).normal_closing_time,
        patch=lambda value: BuildEditPatch(normal_closing_time=value),
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "normal_opening_time",
        OPTIONAL_INT_FORMAT,
        tr(t"""
            bot-build-field-normal-opening-time =
                .label = Normal Opening Time
                .placeholder = in gameticks
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-normal-opening-time =
                .label = Normal Opening Time
                .placeholder = in gameticks
            """).attribute("label"),
        reader=lambda build: _door(build).normal_opening_time,
        patch=lambda value: BuildEditPatch(normal_opening_time=value),
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "creators_ign",
        STRING_LIST_FORMAT,
        tr(t"""
            bot-build-field-creators-ign =
                .label = Creators Ign
                .placeholder = Me, My Dog
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-creators-ign =
                .label = Creators Ign
                .placeholder = Me, My Dog
            """).attribute("label"),
        reader=lambda build: list(build.creators_ign),
        patch=lambda value: BuildEditPatch(creators_ign=value),
    ),
    field_spec(
        "image_urls",
        STRING_LIST_FORMAT,
        tr(t"""
            bot-build-field-image-urls =
                .label = Image Urls
                .placeholder = any urls, comma separated
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-image-urls =
                .label = Image Urls
                .placeholder = any urls, comma separated
            """).attribute("label"),
        reader=lambda build: list(build.image_urls),
        patch=lambda value: BuildEditPatch(image_urls=value),
        parser=parse_web_urls,
    ),
    field_spec(
        "video_urls",
        STRING_LIST_FORMAT,
        tr(t"""
            bot-build-field-video-urls =
                .label = Video Urls
                .placeholder = any urls, comma separated
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-video-urls =
                .label = Video Urls
                .placeholder = any urls, comma separated
            """).attribute("label"),
        reader=lambda build: list(build.video_urls),
        patch=lambda value: BuildEditPatch(video_urls=value),
        parser=parse_web_urls,
    ),
    field_spec(
        "world_download_urls",
        STRING_LIST_FORMAT,
        tr(t"""
            bot-build-field-world-download-urls =
                .label = World Download Urls
                .placeholder = any urls, comma separated
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-world-download-urls =
                .label = World Download Urls
                .placeholder = any urls, comma separated
            """).attribute("label"),
        reader=lambda build: list(build.world_download_urls),
        patch=lambda value: BuildEditPatch(world_download_urls=value),
        parser=parse_web_urls,
    ),
    field_spec(
        "completion_time",
        OPTIONAL_TEXT_FORMAT,
        tr(t"""
            bot-build-field-completion-time =
                .label = Completion Time
                .placeholder = Any time format works
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-completion-time =
                .label = Completion Time
                .placeholder = Any time format works
            """).attribute("label"),
        reader=lambda build: build.completion_time,
        patch=lambda value: BuildEditPatch(completion_time=value),
    ),
    field_spec(
        "extra_user_info",
        OPTIONAL_TEXT_FORMAT,
        tr(t"""
            bot-build-field-extra-user-info =
                .label = Extra User Info
                .placeholder = Anything a reader should know
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-extra-user-info =
                .label = Extra User Info
                .placeholder = Anything a reader should know
            """).attribute("label"),
        reader=lambda build: build.description,
        patch=lambda value: BuildEditPatch(extra_user_info=value),
        display=FieldDisplay.PARAGRAPH,
    ),
    field_spec(
        "server_ip",
        OPTIONAL_TEXT_FORMAT,
        tr(t"""
            bot-build-field-server-ip =
                .label = Server Ip
                .placeholder = play.example.com
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-server-ip =
                .label = Server Ip
                .placeholder = play.example.com
            """).attribute("label"),
        reader=lambda build: _server_info_value(build, "server_ip"),
        patch=lambda value: BuildEditPatch(server_ip=value),
    ),
    field_spec(
        "coordinates",
        OPTIONAL_TEXT_FORMAT,
        tr(t"""
            bot-build-field-coordinates =
                .label = Coordinates
                .placeholder = x y z
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-coordinates =
                .label = Coordinates
                .placeholder = x y z
            """).attribute("label"),
        reader=lambda build: _server_info_value(build, "coordinates"),
        patch=lambda value: BuildEditPatch(coordinates=value),
    ),
    field_spec(
        "command_to_get_to_build",
        OPTIONAL_TEXT_FORMAT,
        tr(t"""
            bot-build-field-command-to-get-to-build =
                .label = Command To Get To Build
                .placeholder = /warp door
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-command-to-get-to-build =
                .label = Command To Get To Build
                .placeholder = /warp door
            """).attribute("label"),
        reader=lambda build: _server_info_value(build, "command_to_build"),
        patch=lambda value: BuildEditPatch(command_to_get_to_build=value),
    ),
)
"""Every entry owns its typed read and patch operations; UI keys carry no mutation authority."""


def _submitted_text(values: Mapping[str, object], key: str) -> str:
    """Read a dynamic text field, restoring the blank string the build parser expects."""
    value = values[key]
    if value is None:
        return ""
    if not isinstance(value, str):
        message = f"build field {key!r} submitted a non-text value"
        raise TypeError(message)
    return value


def _edit_form(items: Sequence[BoundBuildField[Any]], page: int) -> sl.forms.FormSpec:
    page_items = items[5 * (page - 1) : 5 * page]
    fields: list[sl.forms.FormField[Any]] = []
    for item in page_items:
        spec = item.spec
        field_type = sl.forms.TextAreaField[Any] if spec.display is FieldDisplay.PARAGRAPH else sl.forms.TextField[Any]
        fields.append(
            field_type(
                key=item.key,
                label=spec.label,
                placeholder=spec.placeholder,
                default=item.current_text,
                required=spec.required,
                minimum=spec.minimum,
                maximum=spec.maximum,
            )
        )

    def validate(values: Mapping[str, object]) -> tuple[sl.forms.FormIssue, ...]:
        errors: list[sl.forms.FormIssue] = []
        for item, field in zip(page_items, fields, strict=True):
            try:
                item.spec.parser(_submitted_text(values, item.key))
            except ValueError as error:
                errors.append(
                    sl.forms.FieldError(
                        field, message=str(error) or tr(t"bot-submission-ui-views-invalid-value = Invalid value")
                    )
                )
        return tuple(errors)

    return sl.forms.FormSpec(
        tr(t"bot-submission-ui-views-edit-build-section = Edit build, section {page}"),
        tuple(fields),
        validator=validate,
    )


class BuildEditScreen(sd.Screen[int]):
    """Stage local field changes, review them, and save one combined build patch.

    The build preview follows topic updates, while staged fields remain local.
    Reloading or opening a fresh editor discards those fields. Saving checks current
    authority and supplies the displayed build revision to the edit service. Completion
    returns the saved build ID; closing without saving cancels the screen.
    """

    policy = sd.ResponseSpec(
        timeout=900, live_updates=True, retain_routed_on_timeout=True, session=sd.SessionSpec("build-edit")
    )

    page: int = sl.state(1)
    confirming: bool = sl.state(default=False)
    saved: bool = sl.state(default=False)
    validation_error: sl.TextLike | None = sl.state(None)

    def __init__(
        self,
        build: Build,
        builds: BuildService,
        items: Sequence[BoundBuildField[Any]] | DEFAULT = DEFAULT,
        *,
        node: sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None = None,
        authorize: Callable[[], Awaitable[bool]],
        render_build: Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]],
        refresh_posts: Callable[[int], Awaitable[None]],
        changes: TopicHandle | None = None,
        recovered: bool = False,
    ) -> None:
        self._seed: tuple[Build, sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None] | None = (build, node)
        self._changes = changes
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
    async def projection(self) -> tuple[Build, sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None]:
        """Load the edited build and keep its preview current with the build topic."""
        if self._changes is not None:
            self._changes.watch()
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

    def _current(self) -> tuple[Build, sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None]:
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

    def _replace(self, build: Build, node: sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None) -> None:
        if self._seed is not None:
            self._seed = (build, node)
        else:
            self.projection.replace((build, node))

    @property
    def max_pages(self) -> int:
        return max(1, (len(self.items) + 4) // 5)

    def stage(self, key: str, text: str) -> bool:
        """Stage an applicable field and return whether its key exists.

        True does not imply valid input; parse failures are retained in validation_error.
        """
        for item in self.items:
            if item.key == key:
                item.stage(text)
                errors = [error for error in (self.validation_error, item.validation_error) if error]
                self.validation_error = Message.join("\n", errors) if errors else None
                return True
        return False

    async def may_edit(self) -> bool:
        """Recheck whether the actor may currently edit this build."""
        return await self._authorize()

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self.saved:
            return (
                sl.section(
                    sl.heading(tr(t"bot-submission-ui-views-changes-saved = Changes saved")),
                    sl.paragraph(tr(t"build-editor-card-refreshed = The build card has been refreshed.")),
                ),
            )
        state = self.projection.status
        if self._seed is None and not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(tr(t"bot-submission-ui-views-loading-build = Loading build.")),)
        page = self.page
        pages = self.max_pages
        validation_error = self.validation_error
        description = (
            tr(t"build-editor-section-unsaved-indicator = Section {page} of {pages}. Filled dots have unsaved changes.")
            if not validation_error
            else tr(t"""
                build-editor-validation-errors =
                    Fix these values before review:
                    {validation_error}
                """)
        )
        controls: list[sl.semantic.ActionControl[sd.Request]] = [
            sl.action_control(
                tr(t"bot-submission-ui-views-edit-this-section = Edit this section"), self._open, key="open"
            ),
            sl.action_control(
                tr(t"bot-submission-ui-views-previous = Previous"),
                self._previous,
                key="previous",
                available=self.page != 1,
            ),
            sl.action_control(
                tr(t"bot-submission-ui-views-next = Next"),
                self._next,
                key="next",
                available=self.page != self.max_pages,
            ),
        ]
        if self.confirming:
            controls.extend(
                (
                    sl.action_control(
                        tr(t"bot-submission-ui-views-apply-changes = Apply changes"),
                        self._apply,
                        key="apply",
                        tone=sl.Tone.SUCCESS,
                    ),
                    sl.action_control(tr(t"bot-submission-ui-views-back = Back"), self._unconfirm, key="unconfirm"),
                )
            )
        else:
            controls.append(
                sl.action_control(
                    tr(t"bot-submission-ui-views-review-changes = Review changes"),
                    self._review,
                    key="review",
                    tone=sl.Tone.SUCCESS,
                )
            )
        if self.validation_error:
            controls.append(
                sl.action_control(
                    tr(t"bot-submission-ui-views-reload-latest = Reload latest"), self._reload, key="reload"
                )
            )
        controls.append(sl.action_control(tr(t"bot-submission-ui-views-close = Close"), self._close, key="close"))
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = []
        if self._recovered:
            nodes.append(
                sl.status(
                    tr(
                        t"build-editor-reloaded-discarded-changes = Fresh "
                        t"editor loaded. Unsaved changes from the previous editor were discarded."
                    ),
                    tone=sl.Tone.WARNING,
                )
            )
        nodes.append(
            sl.section(
                sl.heading(tr(t"bot-submission-ui-views-edit-build = Edit build")),
                sl.truncate(sl.paragraph(description)),
                sl.fields(
                    sl.field(
                        tr(t"bot-submission-ui-views-fields-in-this-section = Fields in this section") + "\n",
                        self.summary_text(),
                    )
                ),
                sl.note(
                    tr(
                        t"build-editor-reload-discards-staged-context = Reloading a "
                        t"fresh editor discards every staged context in this one."
                    )
                ),
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
                        tr(t"bot-submission-ui-views-reload-fresh-editor = Reload fresh editor"),
                        build_controls.recover_build_editor.id(build_id=self._build_id),
                        key="restart",
                    ),
                    key="build-edit-recovery",
                )
            )
        return tuple(nodes)

    def summary_text(self) -> sl.TextLike:
        page_items = self.items[5 * (self.page - 1) : 5 * self.page]
        return Message.join("\n", (("● " if item.modified else "○ ") + item.summary for item in page_items))

    async def _open(self, event: sl.PressEvent) -> None:
        if await self._may_event(event):
            await event.present_form(
                _edit_form(self.items, self.page),
                key="edit",
                on_submit=self._edited,
            )

    async def _edited(self, event: sl.SubmitEvent) -> None:
        errors: list[sl.TextLike] = []
        for item in self.items[5 * (self.page - 1) : 5 * self.page]:
            item.stage(_submitted_text(event.values, item.key))
            if item.validation_error:
                label = item.spec.label
                detail = item.validation_error
                errors.append(tr(t"bot-build-field-error = **{label}:** {detail}"))
        self.validation_error = Message.join("\n", errors) if errors else None
        if errors:
            error_text = Message.join("\n", errors)
            await event.notice(
                tr(t"""
                build-editor-submit-validation-errors =
                    Fix these values before review:
                    {error_text}
                """)
            )
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
            self.validation_error = tr(t"bot-submission-ui-views-no-changes-to-review-yet = No changes to review yet.")
            return
        self.confirming = True

    async def _unconfirm(self, event: sl.PressEvent) -> None:
        self.confirming = False

    async def _apply(self, event: sd.PressEvent) -> None:
        if not await self._may_event(event):
            return
        changed = [item for item in self.items if item.modified]
        await event.acknowledge()
        patch = BuildEditPatch.combine(item.to_patch() for item in changed)
        edited_build_id: int | None = None
        build = self.build
        if build.id is None:
            patch.apply(build)
            actor = await subject_for_interaction(cast(sd.Request[Any], event.context))
            if actor.account_id is None:
                raise AuthorizationError
            await self.builds.save(
                build,
                actor=actor.account_id,
                operation="create",
                source="discord_editor",
            )
            self._build_id = build.id
        else:
            try:
                actor = BuildEditor(
                    await subject_for_interaction(cast(sd.Request[Any], event.context)), source="discord_editor"
                )
                build = await self.builds.apply_edit(actor, build.id, patch, expected_revision=build.revision)
            except BuildRevisionMismatchError:
                self.confirming = False
                self.validation_error = tr(
                    t"build-editor-concurrent-change = This build "
                    t"changed while you were editing. Reload the latest version; your staged changes will "
                    t"be discarded."
                )
                self.invalidate()
                return
            edited_build_id = build.id
        self.saved = True
        self.confirming = False
        self._replace(build, await self._render_build(build))
        if edited_build_id is not None:
            await self._refresh_posts(edited_build_id)
        assert build.id is not None
        self.complete(event, build.id)

    async def _close(self, event: sd.PressEvent) -> None:
        self.cancel(event)

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
            await event.notice(
                tr(
                    t"build-editor-edit-access-required-views = Only the pending build's "
                    t"submitter or a trusted staff member can edit it."
                )
            )
            return False
        return True
