"""Editing a build form's sections in Discord, shared by draft and saved-build editors.

A build form groups its fields into sections such as "Name and creators" or "Door size and type"
(see `squid.submissions.application.forms`). An editor shows one row per section with an Edit
button. Editing a section opens either a modal, when its fields fit one (at most five typed
fields), or a separate private message (SectionEditorScreen) with paged select menus for
catalogue fields, which can hold more options than a modal or one select menu allows.

The code here does not know whether it edits a draft or a saved build. It reads and writes
through an `AnswerWorkspace`; the draft workspace saves each change at once, while a saved
build's workspace keeps changes until the user reviews and saves them.

The manifest stores a size as three fields, such as opening_width, opening_height, and
opening_depth; editors show and edit each triple as one "5 x 7 x 4" value (see DimensionGroup).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from typing import Any, Protocol, cast, override

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.parse import format_dimensions, parse_dimensions
from squid.bot.ui import DISCORD_BLUE
from squid.builds.application.input import parse_build_ticks
from squid.core.errors import JSONValue
from squid.core.i18n import tr
from squid.submissions.domain import (
    ChoiceOption,
    FormField,
    FormManifest,
    FormSection,
    SubmissionOrigin,
    ValueKind,
)
from squid.submissions.domain.finalization import (
    SubmissionAttentionReason,
)
from squid_i18n import Text, md_tr
from squid_ui.interactions import PressHandler
from squid_ui.text import TextLike, md

MAX_MODAL_FIELDS = 5
"""Discord modals hold at most five inputs."""


PICKER_PAGE_SIZE = 25
"""Discord select menus hold at most 25 options."""


_VALUE_PREVIEW_LENGTH = 100
"""Longer answers, such as descriptions, are shortened in the summary rows."""


@dataclass(frozen=True, slots=True)
class DimensionGroup:
    """Three manifest fields edited as one "width x height x depth" input.

    The input accepts what parse_dimensions accepts: two or three numbers separated by x or *,
    with ? for an unknown value. A missing or unknown part is saved as unset; if the form
    requires that part, the summary flags it like any other required field.
    """

    key: str
    """The modal input key; it must not collide with a manifest field ID."""
    label: Text
    field_ids: tuple[str, str, str]
    """Width, height, and depth field IDs, in that order."""


DIMENSION_GROUPS = (
    DimensionGroup(
        "capture_size",
        tr(t"draft-editor-build-size = Build size"),
        ("capture_width", "capture_height", "capture_depth"),
    ),
    DimensionGroup(
        "opening_size",
        tr(t"draft-editor-door-size = Door size"),
        ("opening_width", "opening_height", "opening_depth"),
    ),
)


DETAIL_SESSION = sd.SessionSpec("draft-detail")
"""Section and file editors share one session, so opening one closes the editor message already open."""


class AnswerWorkspace(Protocol):
    """The answers one editor changes, shared by every message that editor opens.

    `answers` includes the form's defaults; `stored_answers` holds only what was set, which a
    multi-page picker needs so it keeps choices from pages it is not showing.
    """

    @property
    def manifest(self) -> FormManifest: ...

    @property
    def origin(self) -> SubmissionOrigin:
        """The client the form is shown to, which decides some fields' visibility."""
        ...

    @property
    def category(self) -> str: ...

    @property
    def answers(self) -> dict[str, JSONValue]: ...

    @property
    def stored_answers(self) -> Mapping[str, JSONValue]: ...

    @property
    def revision(self) -> int:
        """The revision a modal saves against, so a stale modal cannot overwrite a newer change."""
        ...

    @property
    def fields(self) -> tuple[FormField, ...]:
        """Fields visible for the current answers, in form order."""
        ...

    @property
    def sections(self) -> tuple[FormSection, ...]:
        """Sections reduced to their visible fields; sections with none are left out."""
        ...

    @property
    def editable(self) -> bool:
        """Whether the answers can still change; editor messages close themselves once they cannot."""
        ...

    @property
    def notice(self) -> TextLike:
        """An error from the last change, shown until the next change replaces it."""
        ...

    @property
    def option_sets(self) -> Mapping[str, tuple[ChoiceOption, ...]]:
        """Catalogue choices by option source, loaded by `load_options`."""
        ...

    def section(self, section_id: str) -> FormSection | None: ...

    def history_controls(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        """Undo and Redo for the changes made in this editor."""
        ...

    async def load_options(self, fields: Iterable[FormField]) -> None: ...

    async def save_values(self, values: Mapping[str, JSONValue], *, base_revision: int | None = None) -> None:
        """Change several answers as one step; a failure changes none of them and sets `notice`."""
        ...


class SectionEditorScreen(sd.Screen[None]):
    """A private message for one section whose catalogue fields need paged selects.

    Typed fields in the section still use modals, from Edit buttons beside their values.
    Choices save immediately; Done only closes this message.
    """

    policy = sd.ResponseSpec(audience="personal", timeout=600, live_updates=True, session=DETAIL_SESSION)
    picker_pages: Mapping[str, int] = sl.state({})
    """The page each catalogue field's select menu shows, keyed by field ID."""

    def __init__(self, workspace: AnswerWorkspace, section_id: str) -> None:
        self.workspace = workspace
        self.section_id = section_id

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        ws = self.workspace
        section = ws.section(self.section_id)
        if (closed := closed_detail(self, ws)) is not None:
            return closed
        if section is None:
            return (sl.status(tr(t"draft-editor-section-gone = This section is no longer part of the form.")),)
        answers = ws.answers
        children: list[Any] = [sd.v2.heading(md(t"{section.title}"))]
        if ws.notice:
            children.append(sd.v2.text(Text.join("", ("⚠️ ", ws.notice))))
        # Follow the form's field order: a picker where its field is, and each modal group where it starts.
        chunks = {chunk.fields[0].id: chunk for chunk in modal_chunks(section)}
        for field in section.fields:
            if field.option_source is not None:
                children.extend(self._picker(field, answers.get(field.id)))
            elif (chunk := chunks.get(field.id)) is not None:
                children.append(
                    sd.v2.section(
                        Text.join("\n", _answer_lines(ws, chunk.fields, answers, show_empty=True)),
                        accessory=sd.v2.button(
                            tr(t"draft-editor-edit = Edit"), partial(self._edit_form, chunk.id), key=f"form_{chunk.id}"
                        ),
                    )
                )
        children.extend((sd.v2.separator(), done_row(self._done)))
        return (sd.v2.panel(*children, accent=DISCORD_BLUE), *self.workspace.history_controls())

    def _picker(self, field: FormField, value: JSONValue) -> list[Any]:
        """A catalogue field's current choice and one page of its options."""
        options = self.workspace.option_sets.get(field.option_source or "", ())
        selected = _as_list(value)
        labels = {option.value: option.label for option in options}
        chosen = ", ".join(labels.get(item, item) for item in selected)
        name = (
            md_tr(t"draft-editor-picker-required = **{(label := field.label)}** · Required")  # noqa: F841 - captured Fluent binding
            if field.required
            else md(t"**{field.label}**")
        )
        lines: list[TextLike] = [
            name,
            md(t"{chosen}") if chosen else md_tr(t"draft-editor-nothing-chosen = *Nothing chosen yet.*"),
        ]
        if not options:
            lines.append(tr(t"draft-editor-no-options = No options are available right now."))
            return [sd.v2.text(Text.join("\n", lines))]
        pages = (len(options) - 1) // PICKER_PAGE_SIZE + 1
        page = self._picker_page(field, options)
        multiple = field.value_kind is ValueKind.STRING_LIST
        shown = options[page * PICKER_PAGE_SIZE : (page + 1) * PICKER_PAGE_SIZE]
        if pages > 1:
            page_number = page + 1
            lines.append(
                md_tr(
                    t"draft-editor-picker-pages-multiple = -# Page {page_number} of {pages}. "
                    t"Choices on other pages are kept.",
                )
                if multiple
                else md_tr(t"draft-editor-picker-pages = -# Page {page_number} of {pages}.")
            )
        children: list[Any] = [
            sd.v2.text(Text.join("\n", lines)),
            sd.v2.select(
                *(sd.v2.option(option.label[:100], option.value, default=option.value in selected) for option in shown),
                on_select=partial(self._select_value, field.id),
                key=f"pick_{field.id}",
                placeholder=tr(t"draft-editor-choose = Choose…"),
                min_values=1 if field.required and not multiple else 0,
                max_values=len(shown) if multiple else 1,
            ),
        ]
        if pages > 1:
            children.append(
                sd.v2.row(
                    sd.v2.button(
                        tr(t"draft-editor-previous-page = Previous page"),
                        partial(self._turn_page, field.id, page, -1),
                        key=f"previous_{field.id}",
                        disabled=page == 0,
                    ),
                    sd.v2.button(
                        tr(t"draft-editor-next-page = Next page"),
                        partial(self._turn_page, field.id, page, 1),
                        key=f"next_{field.id}",
                        disabled=page == pages - 1,
                    ),
                )
            )
        return children

    def _picker_page(self, field: FormField, options: Sequence[ChoiceOption]) -> int:
        """The page a picker shows: the one last turned to, else the page with the first choice."""
        pages = (len(options) - 1) // PICKER_PAGE_SIZE + 1
        if field.id in self.picker_pages:
            return min(self.picker_pages[field.id], pages - 1)
        selected = _as_list(self.workspace.stored_answers.get(field.id))
        first = next((index for index, option in enumerate(options) if option.value in selected), 0)
        return first // PICKER_PAGE_SIZE

    async def _edit_form(self, chunk_id: str, event: sd.PressEvent) -> None:
        section = self.workspace.section(self.section_id)
        chunk = next((chunk for chunk in modal_chunks(section) if chunk.id == chunk_id), None) if section else None
        if chunk is not None:
            await present_chunk_form(self.workspace, chunk, event)

    async def _select_value(self, field_id: str, event: sd.SelectionEvent) -> None:
        ws = self.workspace
        field = next(field for field in ws.fields if field.id == field_id)
        value: JSONValue
        if field.value_kind is ValueKind.STRING_LIST:
            options = ws.option_sets.get(field.option_source or "", ())
            page = self._picker_page(field, options)
            visible = {option.value for option in options[page * PICKER_PAGE_SIZE : (page + 1) * PICKER_PAGE_SIZE]}
            # A select menu reports only its own page; keep the choices made on other pages.
            retained = [item for item in _as_list(ws.stored_answers.get(field.id)) if item not in visible]
            value = [*retained, *event.values]
        else:
            value = event.values[0] if event.values else None
        await ws.save_values({field.id: value})

    async def _turn_page(self, field_id: str, current: int, step: int, event: sd.PressEvent) -> None:
        self.picker_pages = {**self.picker_pages, field_id: max(0, current + step)}

    async def _done(self, event: sd.PressEvent) -> None:
        self.complete(event, None)


def closed_detail(
    screen: sd.Screen[None], ws: AnswerWorkspace
) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...] | None:
    """The final view of an editor message once it is closed, or once its answers can no longer change."""
    if screen.outcome is not None:
        return (sl.status(tr(t"editor-detail-closed = Done. You can dismiss this message.")),)
    if not ws.editable:
        return (sl.status(tr(t"editor-detail-locked = These answers can no longer be edited here.")),)
    return None


def done_row(on_done: PressHandler[sd.Request], *before: Any) -> Any:
    return sd.v2.row(
        *before,
        sd.v2.button(tr(t"draft-editor-done = Done"), on_done, key="done", style=sl.primitives.ActionStyle.PRIMARY),
    )


def form_sections(manifest: FormManifest, category: str, fields: Iterable[FormField]) -> tuple[FormSection, ...]:
    """The form's sections in order, reduced to `fields`; sections left with no field are dropped."""
    visible = {field.id for field in fields}
    sections = (
        FormSection(section.id, section.title, tuple(field for field in section.fields if field.id in visible))
        for section in (*manifest.common_sections, *manifest.category(category).sections)
    )
    return tuple(section for section in sections if section.fields)


def section_text(ws: AnswerWorkspace, section: FormSection, *, show_empty: bool = False) -> Text:
    """The section title followed by one line per answer or missing required field.

    With show_empty, empty optional fields get a "Not set" line too, as when reviewing a cleared value.
    """
    lines = _answer_lines(ws, section.fields, ws.answers, show_empty=show_empty)
    if not lines:
        lines = [md_tr(t"draft-editor-section-empty = *Optional. Nothing added yet.*")]
    return Text.join("\n", (md(t"**{section.title}**"), *lines))


def _answer_lines(
    ws: AnswerWorkspace, fields: Sequence[FormField], answers: Mapping[str, JSONValue], *, show_empty: bool
) -> list[TextLike]:
    """Format "Label: value" lines, flagging missing required fields and invalid values.

    Empty optional fields are skipped unless show_empty is set, as in a section's own editor
    where the submitter needs to see every field they can fill in.
    """
    errors = ws.manifest.validate_answers(ws.category, answers, origin=ws.origin)
    groups = dimension_groups(fields)
    grouped = {field_id for group in groups for field_id in group.field_ids}
    lines: list[TextLike] = []
    for field in fields:
        group = next((group for group in groups if group.field_ids[0] == field.id), None)
        if group is not None:
            if (line := _dimensions_line(group, answers, errors, show_empty=show_empty)) is not None:
                lines.append(line)
            continue
        if field.id in grouped:
            continue
        value = answers.get(field.id)
        label = Text.join("", (field.label, ": "))
        if is_empty(value):
            if field.required:
                lines.append(label + md_tr(t"draft-editor-required = ⚠️ *Required*"))
            elif show_empty:
                lines.append(label + md_tr(t"draft-editor-not-set = *Not set*"))
            continue
        line = label + _value_text(ws, field, value)
        if (reason := errors.get(field.id)) is not None:
            line = Text.join("", (line, " — ⚠️ ", problem_text(reason)))
        lines.append(line)
    return lines


def _dimensions_line(
    group: DimensionGroup, answers: Mapping[str, JSONValue], errors: Mapping[str, str], *, show_empty: bool
) -> TextLike | None:
    """Show a size as "Build size: 5 x 7 x ?", flagging missing or invalid parts.

    Returns None for an empty optional size outside a section's own editor, like other empty
    optional fields.
    """
    values = tuple(answers.get(field_id) for field_id in group.field_ids)
    label = Text.join("", (group.label, ": "))
    reason = next((errors[field_id] for field_id in group.field_ids if field_id in errors), None)
    if all(value is None for value in values):
        if reason is not None:
            return label + md_tr(t"draft-editor-required = ⚠️ *Required*")
        return label + md_tr(t"draft-editor-not-set = *Not set*") if show_empty else None
    size = format_dimensions(tuple(value if isinstance(value, int) else None for value in values))
    line = label + md(t"{size}")
    return line if reason is None else Text.join("", (line, " — ⚠️ ", problem_text(reason)))


def _value_text(ws: AnswerWorkspace, field: FormField, value: JSONValue) -> TextLike:
    """Show an answer the way the submitter chose it: option labels, Yes or No, and seconds."""
    if isinstance(value, bool):
        return tr(t"draft-editor-yes = Yes") if value else tr(t"draft-editor-no = No")
    labels = {option.value: option.label for option in field.options}
    if field.option_source is not None:
        labels = {option.value: option.label for option in ws.option_sets.get(field.option_source, ())}
    if isinstance(value, list):
        joined = ", ".join(labels.get(str(item), str(item)) for item in value)
        return md(t"{joined}")
    if field.value_kind is ValueKind.GAME_TICKS and isinstance(value, int):
        return md(t"{_seconds(value)}")
    text = labels.get(str(value), str(value)).replace("\n", " ")
    if len(text) > _VALUE_PREVIEW_LENGTH:
        text = text[: _VALUE_PREVIEW_LENGTH - 1].rstrip() + "…"
    return md(t"{text}")


async def present_chunk_form(ws: AnswerWorkspace, chunk: FormSection, event: sd.PressEvent) -> None:
    """Show a modal for up to five typed fields and save them together."""
    # Save against the revision the modal showed, so a stale modal cannot overwrite a newer edit.
    base_revision = ws.revision
    answers = ws.answers
    groups = dimension_groups(chunk.fields)
    grouped = {field_id for group in groups for field_id in group.field_ids}

    async def submitted(event: sl.SubmitEvent) -> None:
        values: dict[str, JSONValue] = {}
        for group in groups:
            raw = event.values.get(group.key)
            parts = parse_dimensions(str(raw)) if raw else (None, None, None)
            values.update(zip(group.field_ids, parts, strict=True))
        for field in chunk.fields:
            if field.id in grouped:
                continue
            raw = event.values.get(field.id)
            if field.value_kind is ValueKind.STRING_LIST:
                values[field.id] = [line.strip() for line in str(raw or "").splitlines() if line.strip()]
            else:
                values[field.id] = cast(JSONValue, raw)
        await ws.save_values(values, base_revision=base_revision)

    entries: list[sl.forms.FormField[Any]] = []
    for field in chunk.fields:
        group = next((group for group in groups if group.field_ids[0] == field.id), None)
        if group is not None:
            entries.append(_dimensions_entry(group, chunk.fields, answers))
        elif field.id not in grouped:
            entries.append(_form_entry(field, answers.get(field.id)))
    await event.present_form(
        sl.forms.FormSpec(chunk.title[:45], tuple(entries)), key=f"draft-{chunk.id}", on_submit=submitted
    )


@dataclass(frozen=True, slots=True)
class _TicksField(sl.forms.DurationField[Any]):
    """A timing entered as game ticks, redstone ticks, or seconds and stored as game ticks."""

    @override
    def format(self, value: object) -> sl.forms.PrefillValue:
        return _seconds(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _form_entry(field: FormField, value: JSONValue) -> sl.forms.FormField[Any]:
    """Build the modal input for one manifest field, prefilled with its saved value.

    Discord marks required inputs itself, so labels carry no "(required)" suffix.
    """
    label = field.label[:45]
    help_text = field.help_text or (field.label if len(field.label) > 45 else None)
    description = help_text[:100] if help_text else None
    if field.options or field.value_kind is ValueKind.BOOLEAN:
        options = (
            tuple(sl.forms.ChoiceOption(option.value, option.label[:100], option.value) for option in field.options)
            if field.options
            else (
                sl.forms.ChoiceOption("true", tr(t"draft-editor-yes = Yes"), value=True),
                sl.forms.ChoiceOption("false", tr(t"draft-editor-no = No"), value=False),
            )
        )
        return sl.forms.ChoiceField[JSONValue, Any](
            key=field.id, label=label, description=description, required=field.required, default=value, options=options
        )
    if field.value_kind is ValueKind.GAME_TICKS:
        return _TicksField(
            key=field.id,
            label=label,
            description=description,
            required=field.required,
            default=cast(int | None, value),
            placeholder="30gt, 15rt, or 1.5s",
            parser=parse_build_ticks,
        )
    if field.value_kind is ValueKind.INTEGER:
        return sl.forms.IntField[Any](
            key=field.id,
            label=label,
            description=description,
            required=field.required,
            default=cast(int | None, value),
            minimum=cast(int | None, field.constraints.minimum),
            maximum=cast(int | None, field.constraints.maximum),
        )
    if field.value_kind is ValueKind.NUMBER:
        return sl.forms.FloatField[Any](
            key=field.id,
            label=label,
            description=description,
            required=field.required,
            default=cast(float | None, value),
            minimum=field.constraints.minimum,
            maximum=field.constraints.maximum,
        )
    if field.repeatable:
        return sl.forms.TextAreaField[Any](
            key=field.id,
            label=label,
            description=description,
            required=field.required,
            maximum=4000,
            default="\n".join(_as_list(value)),
            placeholder=tr(t"draft-editor-one-per-line = One per line"),
        )
    maximum = min(4000, field.constraints.max_length or 4000)
    # Short answers such as a name fit on one line; longer ones get a multi-line box.
    text_field = sl.forms.TextField[Any] if maximum <= 200 else sl.forms.TextAreaField[Any]
    return text_field(
        key=field.id,
        label=label,
        description=description,
        required=field.required,
        maximum=maximum,
        default=value if isinstance(value, str) else None,
    )


@dataclass(frozen=True, slots=True)
class _DimensionsField(sl.forms.TextField[Any]):
    """A size typed as "5x7x4"; parsing only checks the format and keeps the text."""

    @override
    def parse(self, raw: object) -> str | None:
        if self._optional(raw):
            return None
        text = str(raw).strip()
        try:
            parse_dimensions(text)
        except ValueError as error:
            message = tr(
                t"draft-editor-size-format = Enter the size as width x height x depth, such as 5x7x4. "
                t"Use ? for a value you don't know."
            )
            raise sl.forms.FormValueError(message) from error
        return text


def dimension_groups(fields: Sequence[FormField]) -> tuple[DimensionGroup, ...]:
    """Return the size groups whose three fields are all among fields."""
    present = {field.id for field in fields}
    return tuple(group for group in DIMENSION_GROUPS if present.issuperset(group.field_ids))


def _dimensions_entry(
    group: DimensionGroup, fields: Sequence[FormField], answers: Mapping[str, JSONValue]
) -> sl.forms.FormField[Any]:
    """Build the single modal input for a size, prefilled as "5 x 7 x ?"."""
    values = tuple(answers.get(field_id) for field_id in group.field_ids)
    return _DimensionsField(
        key=group.key,
        label=group.label,
        description=tr(t"draft-editor-size-help = Width x height x depth in blocks. Use ? for a value you don't know."),
        required=any(field.required for field in fields if field.id in group.field_ids),
        default=None
        if all(value is None for value in values)
        else format_dimensions(tuple(value if isinstance(value, int) else None for value in values)),
        placeholder="5x7x4",
    )


def modal_chunks(section: FormSection) -> tuple[FormSection, ...]:
    """Split a section's typed fields into modal-sized groups; catalogue fields are left out."""
    typed = tuple(field for field in section.fields if field.option_source is None)
    return tuple(
        FormSection(
            f"{section.id}_{offset // MAX_MODAL_FIELDS}",
            section.title if len(typed) <= MAX_MODAL_FIELDS else f"{section.title} ({offset // MAX_MODAL_FIELDS + 1})",
            typed[offset : offset + MAX_MODAL_FIELDS],
        )
        for offset in range(0, len(typed), MAX_MODAL_FIELDS)
    )


def problem_text(reason: str) -> Text:
    """Describe why a field's value cannot be accepted, as a short phrase after its value."""
    match reason:
        case SubmissionAttentionReason.REQUIRED:
            return tr(t"draft-editor-problem-required = Required")
        case SubmissionAttentionReason.REQUIRED_VALUE:
            return tr(t"draft-editor-problem-required-value = Must be confirmed")
        case SubmissionAttentionReason.BELOW_MINIMUM:
            return tr(t"draft-editor-problem-below-minimum = Too small")
        case SubmissionAttentionReason.ABOVE_MAXIMUM:
            return tr(t"draft-editor-problem-above-maximum = Too large")
        case SubmissionAttentionReason.TOO_SHORT:
            return tr(t"draft-editor-problem-too-short = Too short")
        case SubmissionAttentionReason.TOO_LONG:
            return tr(t"draft-editor-problem-too-long = Too long")
        case SubmissionAttentionReason.TOO_FEW_ITEMS:
            return tr(t"draft-editor-problem-too-few-items = Needs more entries")
        case SubmissionAttentionReason.TOO_MANY_ITEMS:
            return tr(t"draft-editor-problem-too-many-items = Too many entries")
        case SubmissionAttentionReason.UNKNOWN_OPTION:
            return tr(t"draft-editor-problem-unknown-option = No longer available, choose again")
        case SubmissionAttentionReason.UNKNOWN_FIELD:
            return tr(t"draft-editor-problem-unknown-field = No longer part of the form")
        case _:
            return tr(t"draft-editor-problem-wrong-type = Not a valid value, enter it again")


def _seconds(ticks: int) -> str:
    """Format game ticks as exact seconds, such as 30 as "1.5s", matching build cards."""
    return f"{(Decimal(ticks) / 20).normalize():f}s"


def _as_list(value: JSONValue) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [] if value is None else [str(value)]


def is_empty(value: JSONValue) -> bool:
    return value is None or value == [] or value == ""
