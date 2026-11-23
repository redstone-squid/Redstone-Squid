"""Moderator preview and confirmation for a build category change."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Literal, override

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.edit_controller import EditController
from squid.bot.submission.ui.expandable_size import expandable_size_text
from squid.core.actors import Actor
from squid.core.builds.application.category_edits import BUILD_CATEGORY_EDIT, BuildCategoryChange
from squid.core.builds.application.edit_schema import DoorPatch, ExtenderPatch
from squid.core.builds.application.edits import build_target
from squid.core.builds.domain import BuildCategory
from squid.core.builds.models import Build, DoorBuild, ExtenderBuild
from squid.core.edits.application import EditRunner
from squid.core.edits.domain import EditValues, RevisionConflictError
from squid.core.i18n import tr
from squid_i18n import Text
from squid_ui.ext.discord.target_types import ComponentsV2Target


class BuildCategoryEditor(sd.Screen[int]):
    """Choose a destination, enter any known subtype facts, then confirm."""

    policy = sd.ResponseSpec(audience="personal", timeout=600, session=sd.SessionSpec("build-category"))
    change: BuildCategoryChange | None = sl.state(None)
    conflict: bool = sl.state(False)

    def __init__(
        self,
        build: Build,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        refresh: Callable[[], Awaitable[None]],
    ) -> None:
        self.build = build
        self.controller = EditController(edits, actor, refresh)

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        if self.outcome is not None:
            return (sl.status(tr(t"build-category-saved = Build category changed.")),)
        current = self.build.category.value
        build_id = self.build.id
        change = self.change
        saved = Text.join("\n", (tr(t"build-category-current = Current category: {current}"), *_facts(self.build)))
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [
            sl.heading(tr(t"build-category-heading = Change category for build #{build_id}")),
            sl.paragraph(saved),
        ]
        if self.conflict:
            nodes.append(
                sl.status(
                    tr(
                        t"build-category-conflict = This build changed. Open the category editor again to review the latest build."
                    ),
                    tone=sl.Tone.WARNING,
                )
            )
            return (sd.v2.panel(*nodes),)
        nodes.append(
            sd.v2.select(
                *(
                    sd.v2.option(
                        category.value, category.value, default=change is not None and change.category is category
                    )
                    for category in BuildCategory
                    if category is not self.build.category
                ),
                on_select=self._choose,
                key="category",
                placeholder=tr(t"build-category-choose = Choose the new category"),
            )
        )
        if change is not None:
            destination = change.category.value
            staged = change.target(build_id)
            nodes.append(
                sl.paragraph(
                    Text.join(
                        "\n",
                        (
                            tr(t"build-category-destination = New category: {destination}"),
                            *_facts(staged),
                        ),
                    )
                )
            )
            nodes.append(
                sl.paragraph(
                    tr(
                        t"build-category-preserved = The build ID, credits, links, overall dimensions, and tags carry over. "
                        t"Facts specific to the old category stay in edit history."
                    )
                )
            )
            controls = []
            if isinstance(staged, DoorBuild | ExtenderBuild):
                controls.append(
                    sl.action_control(
                        tr(t"build-category-edit-details = Edit new details"), sd.on_press(self._details), key="details"
                    )
                )
            if isinstance(staged, DoorBuild):
                controls.append(
                    sl.action_control(
                        tr(t"build-category-edit-timings = Edit door timings"),
                        sd.on_press(self._timings),
                        key="timings",
                    )
                )
            controls.append(
                sl.action_control(
                    tr(t"build-category-confirm = Change category"),
                    sd.on_press(self._apply),
                    key="confirm",
                    tone=sl.Tone.WARNING,
                )
            )
            nodes.append(sl.action_controls(*controls, key="category-actions"))
        return (sd.v2.panel(*nodes),)

    async def _choose(self, event: sd.SelectionEvent) -> None:
        category = BuildCategory(event.values[0])
        if category is not self.build.category and (self.change is None or self.change.category is not category):
            self.change = BuildCategoryChange(category=category)

    async def _details(self, event: sd.PressEvent) -> None:
        base = self.change
        if base is None:
            return
        if base.category is BuildCategory.DOOR:
            door = base.door or DoorPatch()
            width, height, depth = door.door_dimensions or (None, None, None)
            fields = (
                sl.forms.ChoiceField(
                    key="orientation",
                    label=tr(t"submissions-application-forms-door-orientation = Door type"),
                    required=False,
                    default=door.orientation,
                    options=tuple(
                        sl.forms.ChoiceOption(value, value, value) for value in ("Door", "Skydoor", "Trapdoor")
                    ),
                ),
                _size_field("width", tr(t"expandable-size-width = Opening width"), width),
                _size_field("height", tr(t"expandable-size-height = Opening height"), height),
                _size_field("depth", tr(t"expandable-size-depth = Opening depth"), depth),
            )

            async def submitted(event: sl.SubmitEvent) -> None:
                if self.change != base:
                    return
                values = event.values
                dimensions = (values["width"], values["height"], values["depth"])
                self.change = BuildCategoryChange(
                    category=base.category,
                    door=DoorPatch.model_validate(
                        door.model_dump(exclude_unset=True)
                        | {
                            "orientation": values["orientation"],
                            "door_dimensions": dimensions,
                        }
                    ),
                )

        elif base.category is BuildCategory.EXTENDER:
            extender = base.extender or ExtenderPatch()
            fields = (
                sl.forms.ChoiceField(
                    key="orientation",
                    label=tr(t"submissions-application-forms-movement-orientation = Direction"),
                    required=False,
                    default=extender.orientation,
                    options=tuple(
                        sl.forms.ChoiceOption(value, value, value) for value in ("Horizontal", "Upward", "Downward")
                    ),
                ),
                _size_field(
                    "extension_length",
                    tr(t"submissions-application-forms-extension-length = Extension length"),
                    extender.extension_length,
                ),
                _tick_field(
                    "extension_time",
                    tr(t"build-category-extension-ticks = Extension (game ticks)"),
                    extender.extension_time,
                ),
                _tick_field(
                    "retraction_time",
                    tr(t"build-category-retraction-ticks = Retraction (game ticks)"),
                    extender.retraction_time,
                ),
            )

            async def submitted(event: sl.SubmitEvent) -> None:
                if self.change == base:
                    self.change = BuildCategoryChange(
                        category=base.category, extender=ExtenderPatch.model_validate(event.values)
                    )

        else:
            return
        await event.present_form(
            sl.forms.FormSpec(tr(t"build-category-new-details = New category details"), fields),
            key="category-details",
            on_submit=submitted,
        )

    async def _timings(self, event: sd.PressEvent) -> None:
        base = self.change
        if base is None or base.category is not BuildCategory.DOOR:
            return
        door = base.door or DoorPatch()
        labels = {
            "opening_time": tr(t"build-category-opening-ticks = Opening (game ticks)"),
            "closing_time": tr(t"build-category-closing-ticks = Closing (game ticks)"),
            "visible_opening_time": tr(t"build-category-visible-opening-ticks = Visible opening (game ticks)"),
            "visible_closing_time": tr(t"build-category-visible-closing-ticks = Visible closing (game ticks)"),
        }

        async def submitted(event: sl.SubmitEvent) -> None:
            if self.change == base:
                self.change = BuildCategoryChange(
                    category=base.category,
                    door=DoorPatch.model_validate(door.model_dump(exclude_unset=True) | dict(event.values)),
                )

        fields = tuple(_tick_field(key, label, getattr(door, key)) for key, label in labels.items())
        await event.present_form(
            sl.forms.FormSpec(tr(t"build-category-door-timings = Door timings"), fields),
            key="category-timings",
            on_submit=submitted,
        )

    async def _apply(self, event: sd.PressEvent) -> None:
        if self.conflict or self.change is None:
            return
        await event.acknowledge()
        build_id = self.build.id
        try:
            await self.controller.bind(BUILD_CATEGORY_EDIT, build_target(build_id), self.build.revision).save(
                EditValues.of(self.change.model_dump(mode="json", exclude_unset=True)),
                tr(t"build-category-history = Change build category"),
            )
        except RevisionConflictError:
            self.conflict = True
            return
        self.complete(event, build_id)


def _size_field(key: str, label: Text, value: int | None) -> sl.forms.IntField[Literal[False]]:
    return sl.forms.IntField(key=key, label=label, required=False, minimum=1, maximum=512, default=value)


def _tick_field(key: str, label: Text, value: int | None) -> sl.forms.IntField[Literal[False]]:
    return sl.forms.IntField(key=key, label=label, required=False, minimum=0, maximum=2**63 - 1, default=value)


def _facts(build: Build) -> tuple[Text, ...]:
    details: Mapping[str, object]
    if isinstance(build, DoorBuild):
        details = {
            "Door orientation": build.orientation,
            "Opening width": build.door_width,
            "Opening height": build.door_height,
            "Opening depth": build.door_depth,
            "Opening time (gt)": build.normal_opening_time,
            "Visible opening time (gt)": build.visible_opening_time,
            "Closing time (gt)": build.normal_closing_time,
            "Visible closing time (gt)": build.visible_closing_time,
        }
    elif isinstance(build, ExtenderBuild):
        details = {
            "Direction": build.orientation,
            "Extension length": build.extension_length,
            "Extension time (gt)": build.extension_time,
            "Retraction time (gt)": build.retraction_time,
        }
    else:
        return ()
    lines = tuple(Text(t"{label}: {value}") for label, value in details.items() if value is not None)
    if isinstance(build, DoorBuild) and build.expansion is not None:
        return (*lines, expandable_size_text(build.expansion))
    return lines
