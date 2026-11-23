"""Edit adjustable door sizes alongside measured opening dimensions."""

import json
from collections.abc import Sequence
from functools import partial
from itertools import zip_longest
from typing import TYPE_CHECKING, Any, Literal, cast, override

from pydantic import ValidationError

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.submission.ui.sections import SectionEditorScreen, closed_detail
from squid.core.builds.domain.expandable_size import ExpandableSize
from squid.core.builds.domain.expansion import DoorExpansion
from squid.core.i18n import tr
from squid.core.submissions.application.forms import DOOR_GEOMETRY_SECTION_ID
from squid_i18n import Text
from squid_ui.ext.discord.target_types import ComponentsV2Target

if TYPE_CHECKING:
    from squid.bot.submission.ui.build_edit import BuildEditWorkspace


_AXIS = {
    "width": tr(t"expandable-size-width = Opening width"),
    "height": tr(t"expandable-size-height = Opening height"),
    "depth": tr(t"expandable-size-depth = Opening depth"),
}
_MAXIMUM = {
    "finite": tr(t"expandable-size-finite = Finite"),
    "unknown": tr(t"expandable-size-unknown = Unknown"),
    "unbounded": tr(t"expandable-size-unbounded = Unbounded"),
}
_GROUPS = {
    "parameters": tr(t"expandable-size-parameters = Parameters"),
    "branches": tr(t"expandable-size-branches = Branches"),
    "evidence": tr(t"expandable-size-evidence = Source evidence"),
}
type Group = Literal["parameters", "branches", "evidence"]


def expandable_size_text(expansion: DoorExpansion | None) -> Text:
    """Describe the declared size independently of the measured opening."""
    if expansion is None:
        return tr(t"expandable-size-unset = Expandable size: Not set")
    size = ExpandableSize.from_expansion(expansion)
    if size is None:
        parameters, branches = len(expansion.parameters), len(expansion.branches)
        return tr(
            t"expandable-size-advanced-summary = Expandable size: Advanced ({parameters} parameters, {branches} branches)"
        )
    axis, minimum, maximum, step = _AXIS[size.axis], size.minimum, size.maximum, size.step
    if maximum is None:
        bound = _MAXIMUM[size.maximum_kind]
        return tr(t"expandable-size-open-range = {axis}: from {minimum} blocks, step {step}, maximum {bound}")
    return tr(t"expandable-size-finite-range = {axis}: {minimum} to {maximum} blocks, step {step}")


class DoorSizeEditor(SectionEditorScreen):
    """Keep measured dimensions and the supported size range together."""

    section_id_key = DOOR_GEOMETRY_SECTION_ID

    @staticmethod
    def summary(workspace: BuildEditWorkspace) -> Text:
        return expandable_size_text(workspace.expansion)

    @staticmethod
    def review(workspace: BuildEditWorkspace) -> Text | None:
        if workspace.expansion == workspace.saved_expansion:
            return None
        previous, proposed = workspace.saved_expansion, workspace.expansion
        before, after = expandable_size_text(previous), expandable_size_text(proposed)
        lines = [tr(t"expandable-size-review = {before} → {after}")]
        if (
            previous is not None
            and proposed is not None
            and (ExpandableSize.from_expansion(previous) is None or ExpandableSize.from_expansion(proposed) is None)
        ):
            old, new = previous.model_dump(), proposed.model_dump()
            if old["raw_description"] != new["raw_description"]:
                lines.append(tr(t"expandable-size-description-changed = Description changed."))
            for group, label in _GROUPS.items():
                differences: list[str] = []
                for number, (left, right) in enumerate(zip_longest(old[group], new[group]), 1):
                    if left == right:
                        continue
                    if left is None or right is None:
                        differences.append(str(number))
                    else:
                        fields = ", ".join(key.replace("_", " ") for key in left if left[key] != right[key])
                        differences.append(f"{number} ({fields})")
                if differences:
                    items = ", ".join(differences)
                    if len(items) > 200:
                        items = items[:197] + "..."
                    lines.append(tr(t"expandable-size-items-changed = {label} changed: {items}"))
        return Text.join("\n", lines)

    def __init__(self, workspace: BuildEditWorkspace) -> None:
        super().__init__(workspace, self.section_id_key)
        self._edit_workspace = workspace

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        workspace = self._edit_workspace
        if (closed := closed_detail(self, workspace)) is not None:
            return closed
        current = workspace.expansion
        advanced = current is not None and ExpandableSize.from_expansion(current) is None
        return (
            *super().render(),
            sd.v2.panel(
                sd.v2.text(expandable_size_text(current)),
                sl.action_controls(
                    None
                    if advanced
                    else sl.action_control(
                        tr(t"expandable-size-set = Set expandable size"), sd.on_press(self._basic), key="basic"
                    ),
                    sl.action_control(
                        tr(t"expandable-size-advanced = Advanced"), sd.on_press(self._advanced), key="advanced"
                    ),
                    None
                    if current is None
                    else sl.action_control(
                        tr(t"expandable-size-clear = Clear expandable size"), self._clear, key="clear_expansion"
                    ),
                    key="expandable-size-actions",
                ),
            ),
        )

    async def _basic(self, event: sd.PressEvent) -> None:
        ws = self._edit_workspace
        revision = ws.revision
        size = ExpandableSize.from_expansion(ws.expansion)
        if size is None and ws.expansion is not None:
            return
        fields = (
            sl.forms.ChoiceField(
                key="axis",
                label=tr(t"expandable-size-axis = Opening dimension"),
                default=size.axis if size else "width",
                options=tuple(sl.forms.ChoiceOption(key, value, key) for key, value in _AXIS.items()),
            ),
            sl.forms.IntField(
                key="minimum",
                label=tr(t"expandable-size-minimum = Minimum (blocks)"),
                minimum=1,
                default=size.minimum if size else None,
            ),
            sl.forms.ChoiceField(
                key="maximum_kind",
                label=tr(t"expandable-size-maximum-kind = Maximum kind"),
                default=size.maximum_kind if size else "unknown",
                options=tuple(sl.forms.ChoiceOption(key, value, key) for key, value in _MAXIMUM.items()),
            ),
            sl.forms.IntField(
                key="maximum",
                label=tr(t"expandable-size-maximum = Maximum (if finite)"),
                required=False,
                minimum=1,
                default=size.maximum if size else None,
            ),
            sl.forms.IntField(
                key="step",
                label=tr(t"expandable-size-step = Step (blocks)"),
                minimum=1,
                default=size.step if size else 1,
            ),
        )

        async def submitted(event: sl.SubmitEvent) -> None:
            try:
                expansion = ExpandableSize.model_validate(event.values).to_expansion()
            except ValidationError as error:
                ws.notice = error.errors(include_url=False)[0]["msg"]
                return
            ws.stage_expansion(event.transaction, expansion, base_revision=revision)

        await event.present_form(
            sl.forms.FormSpec(tr(t"expandable-size-form = Expandable size"), fields),
            key="expandable-size",
            on_submit=submitted,
        )

    async def _advanced(self, event: sd.PressEvent) -> None:
        await self.open_child(event, AdvancedExpandableSize(self._edit_workspace), on_result=self._advanced_closed)

    async def _advanced_closed(self, event: sd.ScreenResultEvent[None]) -> None:
        """The advanced editor stages changes in the shared workspace."""

    async def _clear(self, event: sl.ActionEventView) -> None:
        self._edit_workspace.stage_expansion(event.transaction, None)


class AdvancedExpandableSize(sd.Screen[None]):
    """Change one part of the structured document while retaining the other facts."""

    policy = sd.ResponseSpec(audience="personal", timeout=600, session=sd.SessionSpec("expandable-size"))

    def __init__(self, workspace: BuildEditWorkspace) -> None:
        self.workspace = workspace

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        current = self.workspace.expansion or DoorExpansion()
        rows: list[Any] = [
            sl.heading(tr(t"expandable-size-advanced-title = Advanced expandable size")),
            sl.paragraph(self.workspace.notice) if self.workspace.notice else None,
            sd.v2.section(
                current.raw_description[:180]
                if current.raw_description
                else tr(t"expandable-size-no-description = No description"),
                accessory=sd.v2.button(
                    tr(t"expandable-size-description = Description"), self._description, key="description"
                ),
            ),
        ]
        for group, label in _GROUPS.items():
            items: Sequence[Any] = getattr(current, group)
            count = len(items)
            rows.append(
                sd.v2.section(
                    tr(t"expandable-size-item-count = {label}: {count}"),
                    accessory=sd.v2.button(label, partial(self._pick, cast(Group, group)), key=group),
                )
            )
        if self.fragment is not None:
            selected, index, _revision = self.fragment
            label, number = _GROUPS[selected], index + 1
            rows.append(
                sd.v2.section(
                    tr(t"expandable-size-selected = {label}, item {number}"),
                    accessory=sd.v2.button(
                        tr(t"expandable-size-edit-item = Edit item"), self._edit_selected, key="selected-fragment"
                    ),
                )
            )
        rows.append(
            sl.action_controls(
                sl.action_control(tr(t"draft-editor-done = Done"), sd.on_press(self._done), key="done"),
                key="advanced-size-actions",
            )
        )
        return (sd.v2.panel(*rows),)

    async def _description(self, event: sd.PressEvent) -> None:
        current = self.workspace.expansion or DoorExpansion()
        await self._edit_fragment(event, current.raw_description or "", "raw_description")

    async def _pick(self, group: Group, event: sd.PressEvent) -> None:
        current = self.workspace.expansion or DoorExpansion()
        items: Sequence[Any] = getattr(current, group)
        revision = self.workspace.revision
        labels = [
            f"{index + 1}. {getattr(item, 'name', None) or getattr(item, 'label', None) or getattr(item, 'text', None) or '—'}"
            for index, item in enumerate(items[:10])
        ]
        summary = "\n".join(label[:65] for label in labels)
        item_range = sl.forms.IntField(
            key="number",
            label=tr(t"expandable-size-item-number = Item number (next number adds one)"),
            minimum=1,
            maximum=len(items) + 1,
            default=len(items) + 1,
        )

        async def picked(event: sl.SubmitEvent) -> None:
            if revision != self.workspace.revision:
                await event.notice(
                    tr(t"build-editor-stale-form = These answers changed while the form was open. Open it again.")
                )
                return
            index = cast(int, event.values["number"]) - 1
            # Present the chosen fragment on a button in a new private screen. Discord
            # cannot open another modal directly from a modal submission.
            self.fragment = (group, index, revision)

        if not items:
            await self._edit_fragment(event, "", group, index=0)
            return
        await event.present_form(
            sl.forms.FormSpec(_GROUPS[group], (sl.forms.FormText(summary), item_range)),
            key=f"select-{group}",
            on_submit=picked,
        )

    fragment: tuple[Group, int, int] | None = sl.state(None)

    async def _edit_selected(self, event: sd.PressEvent) -> None:
        if self.fragment is None:
            return
        group, index, revision = self.fragment
        if revision != self.workspace.revision:
            self.fragment = None
            await event.notice(
                tr(t"build-editor-stale-form = These answers changed while the form was open. Open it again.")
            )
            return
        items: Sequence[Any] = getattr(self.workspace.expansion or DoorExpansion(), group)
        if index > len(items):
            return
        value = items[index].model_dump(exclude_defaults=True, mode="json") if index < len(items) else None
        text = "" if value is None else json.dumps(value, indent=2, ensure_ascii=False)
        await self._edit_fragment(event, text, group, index=index)

    async def _edit_fragment(
        self, event: sd.PressEvent, text: str, field: Literal["raw_description"] | Group, *, index: int | None = None
    ) -> None:
        ws = self.workspace
        revision = ws.revision
        if len(text) > 4000:
            await event.notice(
                tr(
                    t"expandable-size-fragment-too-long = This item is too long for a Discord input. Use the build API to edit it."
                )
            )
            return
        label = tr(t"expandable-size-description = Description") if field == "raw_description" else _GROUPS[field]
        hint = (
            None
            if field == "raw_description"
            else sl.forms.FormText(
                tr(t"expandable-size-item-hint = Enter one JSON object. Clear the input to remove this item.")
            )
        )

        async def submitted(event: sl.SubmitEvent) -> None:
            if revision != ws.revision:
                await event.notice(
                    tr(t"build-editor-stale-form = These answers changed while the form was open. Open it again.")
                )
                return
            raw = cast(str | None, event.values.get("value"))
            document = (ws.expansion or DoorExpansion()).model_dump(mode="json")
            if field == "raw_description":
                document[field] = raw
            else:
                assert index is not None
                rows = cast(list[Any], document[field])
                try:
                    parsed = json.loads(raw) if raw else None
                except ValueError as error:
                    ws.notice = str(error)
                    return
                if raw and not isinstance(parsed, dict):
                    ws.notice = tr(
                        t"expandable-size-item-invalid = Enter one JSON object or clear the input to remove the item."
                    )
                    return
                if index < len(rows):
                    if parsed is None:
                        rows.pop(index)
                    else:
                        rows[index] = parsed
                elif parsed is not None:
                    rows.append(parsed)
            try:
                updated = DoorExpansion.model_validate(document)
            except ValidationError as error:
                ws.notice = error.errors(include_url=False)[0]["msg"]
                return
            self.fragment = None
            ws.stage_expansion(
                event.transaction, updated if updated != DoorExpansion() else None, base_revision=revision
            )

        field_input = sl.forms.TextAreaField(
            key="value", label=label, required=False, maximum=4000, default=text or None
        )
        await event.present_form(
            sl.forms.FormSpec(label, (field_input,) if hint is None else (hint, field_input)),
            key=f"expansion-{field}",
            on_submit=submitted,
        )

    async def _done(self, event: sd.PressEvent) -> None:
        self.complete(event, None)
