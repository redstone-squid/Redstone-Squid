"""Stage and review patches to existing builds in a Discord editor.

BuildEditScreen keeps field changes local until the user saves one combined patch.
It shares the submission editor's section overview and modal navigation. Submission
drafts save answers as they go; existing builds retain a review step before saving.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from functools import partial
from typing import Any, cast, override

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.edit_controller import EditController
from squid.bot.edit_history import history_controls
from squid.bot.submission.input import parse_web_urls
from squid.bot.submission.ui.editor import BuildEditorScreen, EditorSection
from squid.bot.submission.ui.fields import (
    DIMENSIONS_FORMAT,
    OPTIONAL_INT_FORMAT,
    OPTIONAL_TEXT_FORMAT,
    STRING_LIST_FORMAT,
    TIMING_FORMAT,
    BoundBuildField,
    BuildFieldSpec,
    FieldDisplay,
    field_spec,
)
from squid.bot.ui import DISCORD_BLUE, DISCORD_YELLOW, tr
from squid.builds.application import BuildEditPatch, BuildService
from squid.builds.application.edits import BUILD_EDIT, build_target, patch_values
from squid.builds.application.input import normalize_extender_orientation, validate_extension_length
from squid.builds.domain import BuildCategory, UnresolvedTaxonomy
from squid.builds.models import Build, DoorBuild, ExtenderBuild
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError
from squid.core.sentinel import DEFAULT
from squid.edits.application import EditRunner
from squid.edits.domain import EditCommand, Revision, RevisionConflictError
from squid_reactivity import TopicHandle
from squid_ui.text import Message, md

_DOOR_ONLY = frozenset({BuildCategory.DOOR})
_EXTENDER_ONLY = frozenset({BuildCategory.EXTENDER})


def _door(build: Build) -> DoorBuild:
    if not isinstance(build, DoorBuild):
        message = "door-only field bound to a non-door build"
        raise TypeError(message)
    return build


def _extender(build: Build) -> ExtenderBuild:
    if not isinstance(build, ExtenderBuild):
        msg = "extender-only field bound to a non-extender build"
        raise TypeError(msg)
    return build


def _server_info_value(build: Build, key: str) -> str | None:
    return cast(str | None, build.extra_info.get("server_info", {}).get(key))


EDIT_FIELDS: tuple[BuildFieldSpec[Any], ...] = (
    field_spec(
        "extender_orientation",
        OPTIONAL_TEXT_FORMAT,
        "Horizontal, Up, Down",
        label=tr(t"submissions-application-forms-movement-orientation = Direction"),
        reader=lambda build: _extender(build).orientation,
        patch=lambda value: BuildEditPatch(extender_orientation=value),
        parser=normalize_extender_orientation,
        categories=_EXTENDER_ONLY,
    ),
    field_spec(
        "extension_length",
        OPTIONAL_INT_FORMAT,
        "",
        label=tr(t"submissions-application-forms-extension-length = Extension length"),
        reader=lambda build: _extender(build).extension_length,
        patch=lambda value: BuildEditPatch(extension_length=value),
        parser=lambda text: validate_extension_length(int(text) if text.strip() else None),
        categories=_EXTENDER_ONLY,
    ),
    field_spec(
        "display_name",
        OPTIONAL_TEXT_FORMAT,
        "",
        label=tr(t"submissions-application-forms-display-name = Build name"),
        reader=lambda build: build.display_name,
        patch=lambda value: BuildEditPatch(display_name=value),
        maximum=120,
    ),
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
                .label = Works in versions
                .placeholder = 1.16 - 1.17.3
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-version-spec =
                .label = Works in versions
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
                .label = Patterns
                .placeholder = Full lamp, Funnel
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-door-type =
                .label = Patterns
                .placeholder = Full lamp, Funnel
            """).attribute("label"),
        reader=lambda build: list(
            dict.fromkeys([*build.patterns, *UnresolvedTaxonomy.read(build.extra_info).patterns])
        ),
        patch=lambda value: BuildEditPatch(door_type=value),
    ),
    field_spec(
        "door_orientation_type",
        OPTIONAL_TEXT_FORMAT,
        tr(t"""
            bot-build-field-door-orientation-type =
                .label = Door orientation
                .placeholder = Door, Trapdoor, Skydoor
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-door-orientation-type =
                .label = Door orientation
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
        reader=lambda build: list(
            dict.fromkeys(
                [
                    *build.wiring_placement_restrictions,
                    *UnresolvedTaxonomy.read(build.extra_info).restriction_names("wiring_placement_restrictions"),
                ]
            )
        ),
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
        reader=lambda build: list(
            dict.fromkeys(
                [
                    *build.animated_restrictions,
                    *UnresolvedTaxonomy.read(build.extra_info).restriction_names("animated_restrictions"),
                ]
            )
        ),
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
        reader=lambda build: list(
            dict.fromkeys(
                [
                    *build.component_restrictions,
                    *UnresolvedTaxonomy.read(build.extra_info).restriction_names("component_restrictions"),
                ]
            )
        ),
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
        reader=lambda build: list(
            dict.fromkeys(
                [
                    *build.miscellaneous_restrictions,
                    *UnresolvedTaxonomy.read(build.extra_info).restriction_names("miscellaneous_restrictions"),
                ]
            )
        ),
        patch=lambda value: BuildEditPatch(miscellaneous_restrictions=value),
    ),
    field_spec(
        "normal_closing_time",
        TIMING_FORMAT,
        tr(t"""
            bot-build-field-normal-closing-time =
                .label = Closing time
                .placeholder = 30gt, 15rt, or 1.5s
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-normal-closing-time =
                .label = Closing time
                .placeholder = 30gt, 15rt, or 1.5s
            """).attribute("label"),
        reader=lambda build: _door(build).normal_closing_time,
        patch=lambda value: BuildEditPatch(normal_closing_time=value),
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "normal_opening_time",
        TIMING_FORMAT,
        tr(t"""
            bot-build-field-normal-opening-time =
                .label = Opening time
                .placeholder = 30gt, 15rt, or 1.5s
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-normal-opening-time =
                .label = Opening time
                .placeholder = 30gt, 15rt, or 1.5s
            """).attribute("label"),
        reader=lambda build: _door(build).normal_opening_time,
        patch=lambda value: BuildEditPatch(normal_opening_time=value),
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "visible_opening_time",
        TIMING_FORMAT,
        "30gt, 15rt, or 1.5s",
        label=tr(t"submissions-visible-opening-time = Visible opening time"),
        reader=lambda build: _door(build).visible_opening_time,
        patch=lambda value: BuildEditPatch(visible_opening_time=value),
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "visible_closing_time",
        TIMING_FORMAT,
        "30gt, 15rt, or 1.5s",
        label=tr(t"submissions-visible-closing-time = Visible closing time"),
        reader=lambda build: _door(build).visible_closing_time,
        patch=lambda value: BuildEditPatch(visible_closing_time=value),
        categories=_DOOR_ONLY,
    ),
    field_spec(
        "creators_ign",
        STRING_LIST_FORMAT,
        tr(t"""
            bot-build-field-creators-ign =
                .label = Creators
                .placeholder = Me, My Dog
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-creators-ign =
                .label = Creators
                .placeholder = Me, My Dog
            """).attribute("label"),
        reader=lambda build: list(build.creators_ign),
        patch=lambda value: BuildEditPatch(creators_ign=value),
        required=True,
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
                .label = Description
                .placeholder = Anything a reader should know
            """).attribute("placeholder"),
        label=tr(t"""
            bot-build-field-extra-user-info =
                .label = Description
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


EDIT_SECTIONS: tuple[tuple[str, sl.TextLike, tuple[str, ...]], ...] = (
    (
        "identity",
        tr(t"build-editor-section-identity = Name and creators"),
        ("display_name", "creators_ign", "extra_user_info", "completion_time"),
    ),
    (
        "dimensions_versions",
        tr(t"build-editor-section-dimensions-versions = Build size and versions"),
        ("dimensions", "version_spec"),
    ),
    (
        "door",
        tr(t"build-editor-section-door = Door size and type"),
        ("door_dimensions", "door_orientation_type"),
    ),
    (
        "extender",
        tr(t"submissions-application-forms-extender-movement = Extender size and direction"),
        ("extender_orientation", "extension_length"),
    ),
    ("patterns", tr(t"submissions-application-forms-mechanism-patterns = Patterns"), ("door_type",)),
    (
        "timing",
        tr(t"build-editor-section-timing = Timing"),
        ("normal_opening_time", "visible_opening_time", "normal_closing_time", "visible_closing_time"),
    ),
    (
        "restrictions",
        tr(t"build-editor-section-restrictions = Restrictions"),
        (
            "wiring_placement_restrictions",
            "animated_restrictions",
            "component_restrictions",
            "miscellaneous_restrictions",
        ),
    ),
    ("files", tr(t"build-editor-section-files = Files"), ("image_urls", "video_urls", "world_download_urls")),
    (
        "server",
        tr(t"build-editor-section-server = Server location"),
        ("server_ip", "coordinates", "command_to_get_to_build"),
    ),
)


class BuildEditScreen(BuildEditorScreen[int]):
    """Stage local field changes, review them, and save one combined build patch.

    The loaded build follows topic updates, while staged fields keep their original
    revision. Reloading or opening a fresh editor discards those fields. Saving checks
    current authority and the revision those values came from. Completion
    returns the saved build ID; closing without saving cancels the screen.
    """

    policy = sd.ResponseSpec(
        timeout=900, live_updates=True, retain_routed_on_timeout=True, session=sd.SessionSpec("build-edit")
    )

    history: sl.runtime.History = sl.runtime.history(limit=20)
    staged_history: sl.runtime.History = sl.runtime.history(limit=20)
    items: tuple[BoundBuildField[Any], ...] = sl.state((), persist=False)
    _base_revision: int = sl.state(0)
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
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        render_build: Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]],
        refresh_posts: Callable[[int], Awaitable[None]],
        changes: TopicHandle | None = None,
        recovered: bool = False,
    ) -> None:
        self._seed: tuple[Build, sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None] | None = (build, node)
        self._changes = changes
        self._build_id = build.id
        self._base_revision = build.revision
        self.builds = builds
        self._authorize = authorize
        self._actor = actor
        self.controller = EditController(edits, actor, self._refresh_saved, history=self.history)
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
        self._base_revision = build.revision
        if self._seed is not None:
            self._seed = (build, node)
        else:
            self.projection.replace((build, node))

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
        if self.saved and self._build_id is None:
            return (
                sl.section(
                    sl.heading(tr(t"bot-submission-ui-views-changes-saved = Changes saved")),
                    sl.paragraph(tr(t"build-editor-card-refreshed = The build card has been refreshed.")),
                ),
            )
        if self.expired:
            # Route registration imports the editor through opening.py. Resolve it after initialization.
            from squid.bot.submission.ui import controls as build_controls

            nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
                sl.status(
                    tr(t"build-editor-expired = This editor expired. Unsaved changes were discarded."),
                    tone=sl.Tone.WARNING,
                )
            ]
            if self._build_id is not None:
                nodes.append(
                    sl.action_controls(
                        sl.routed_action_control(
                            tr(t"build-editor-open-new-editor = Open new editor"),
                            build_controls.recover_build_editor.id(build_id=self._build_id),
                            key="restart",
                        ),
                        key="build-edit-recovery",
                    )
                )
            return tuple(nodes)
        state = self.projection.status
        if self._seed is None and not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(tr(t"bot-submission-ui-views-loading-build = Loading build.")),)
        controls: list[sl.semantic.ActionControl[sd.Request]] = [
            sl.action_control(tr(t"draft-editor-preview = Preview"), self._preview, key="preview"),
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
        controls.append(sl.action_control(tr(t"bot-submission-ui-views-cancel = Cancel"), self._close, key="close"))
        nodes = []
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
        description = self.validation_error or (
            tr(t"build-editor-review-hint = Review the changed values below, then apply them.")
            if self.confirming
            else tr(t"build-editor-staging-hint = Edit each section, then review and save. Changes are not saved yet.")
        )
        sections = []
        for key, title, items in self.sections():
            shown = tuple(item for item in items if item.modified) if self.confirming else items
            if not shown:
                continue
            lines = Message.join(
                "\n",
                (
                    Message.join(
                        "",
                        (
                            item.spec.label,
                            ": ",
                            md(t"{item.current_text[:100] + ('…' if len(item.current_text) > 100 else '')}"),
                        ),
                    )
                    for item in shown
                ),
            )
            sections.append(EditorSection(key, Message.join("\n", (Message.join("", ("**", title, "**")), lines))))
        children: list[Any] = [
            sd.v2.heading(tr(t"bot-submission-ui-views-edit-build = Edit build")),
            sd.v2.text(description),
            sd.v2.separator(),
            *self.section_overview(sections),
            sd.v2.separator(),
            sl.action_controls(*controls, key="build-edit-actions", display=sl.semantic.ControlDisplay.INDIVIDUAL),
        ]
        nodes.append(sd.v2.panel(*children, accent=DISCORD_YELLOW if self.validation_error else DISCORD_BLUE))
        nodes.extend(history_controls(self.staged_history))
        if (
            self.controller.pending
            or self.controller.refresh_failed
            or (not any(item.modified for item in self.items) and not self.staged_history.can_redo)
        ):
            nodes.extend(self.controller.controls())
        return tuple(nodes)

    def sections(self) -> tuple[tuple[str, sl.TextLike, tuple[BoundBuildField[Any], ...]], ...]:
        by_key = {item.key: item for item in self.items}
        return tuple(
            (key, title, tuple(by_key[field] for field in fields if field in by_key))
            for key, title, fields in EDIT_SECTIONS
            if any(field in by_key for field in fields)
        )

    async def _edit_section(self, section_id: str, event: sd.PressEvent) -> None:
        if not await self._may_event(event):
            return
        section = next((section for section in self.sections() if section[0] == section_id), None)
        if section is None:
            return
        _, title, items = section
        form = replace(_edit_form(items, 1), title=title)
        await event.present_form(
            form, key=f"edit-{section_id}", on_submit=partial(self._edited, tuple(item.key for item in items))
        )

    async def _preview(self, event: sd.PressEvent) -> None:
        preview = deepcopy(self.build)
        BuildEditPatch.combine(item.to_patch() for item in self.items if item.modified).apply(preview)
        await self.open_child(event, BuildEditPreview(await self._render_build(preview)), on_result=self._child_closed)

    async def _edited(self, keys: tuple[str, ...], event: sl.SubmitEvent) -> None:
        errors: list[sl.TextLike] = []
        previous = self.items
        staged = tuple(replace(item) for item in self.items)
        for item in staged:
            if item.key not in keys:
                continue
            item.stage(_submitted_text(event.values, item.key))
            if item.validation_error:
                label = item.spec.label
                detail = item.validation_error
                errors.append(tr(t"bot-build-field-error = **{label}:** {detail}"))
        self.confirming = False
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
        if staged != previous:
            self.items = staged
            self.staged_history.record(tr(t"build-edit-history-fields = Edit build fields"))
        self.invalidate()

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
        if not changed:
            return
        await event.acknowledge()
        patch = BuildEditPatch.combine(item.to_patch() for item in changed)
        build = self.build
        if build.id is not None:
            try:
                await self.controller.execute(
                    EditCommand(
                        BUILD_EDIT,
                        (Revision(build_target(build.id), self._base_revision),),
                        patch_values(patch),
                    ),
                    tr(t"build-edit-history-save = Save build changes"),
                )
                self.saved = True
            except RevisionConflictError:
                self.confirming = False
                self.validation_error = tr(
                    t"build-editor-concurrent-change = This build "
                    t"changed while you were editing. Reload the latest version; your staged changes will "
                    t"be discarded."
                )
                self.invalidate()
            return
        patch.apply(build)
        actor = await self._actor()
        if actor.account_id is None:
            raise AuthorizationError
        await self.builds.save(build, actor=actor.account_id, operation="create", source="discord_editor")
        self._build_id = build.id
        self.staged_history.clear()
        self.saved = True
        assert build.id is not None
        self.complete(event, build.id)

    async def _refresh_saved(self) -> None:
        assert self._build_id is not None
        current = await self.builds.get(self._build_id)
        if current is None:
            raise LookupError(self._build_id)
        self.staged_history.clear()
        self.items = tuple(item.spec.bind(current) for item in self.items)
        self.confirming = False
        self.validation_error = None
        self._replace(current, await self._render_build(current))
        await self._refresh_posts(self._build_id)

    async def _close(self, event: sd.PressEvent) -> None:
        if self.saved and self._build_id is not None:
            self.complete(event, self._build_id)
        else:
            self.cancel(event)

    async def _reload(self, event: sl.PressEvent) -> None:
        if not await self._may_event(event):
            return
        await event.acknowledge()
        if self._build_id is not None:
            await self.controller.discard(frozenset({build_target(self._build_id).identity}))

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


class BuildEditPreview(sd.Screen[None]):
    """Show the staged build without saving it."""

    policy = sd.ResponseSpec(audience="personal", timeout=600, session=sd.SessionSpec("build-preview"))

    def __init__(self, node: sl.LayoutNode[sl.ComponentsV2Target, sd.Request]) -> None:
        self.node = node

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return (
            self.node,
            sd.v2.row(sd.v2.button(tr(t"draft-editor-done = Done"), self._done, key="done")),
        )

    async def _done(self, event: sd.PressEvent) -> None:
        self.complete(event, None)
