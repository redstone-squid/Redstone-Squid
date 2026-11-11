"""The private Discord panel for filling in and submitting a build draft.

A submission draft stores answers keyed by the field IDs of a pinned FormManifest. The
manifest groups fields into sections such as "Name and creators" or "Door size and type"; this
panel draws one row per section, listing every answer in it with an Edit button beside
the row. Below the sections, a Files row lists the schematic, images, and videos
attached to the draft.

Editing a section works in one of two ways:

- If every field in it fits in a Discord modal (at most five typed fields), Edit opens
  the modal directly.
- If the section has catalogue fields, such as patterns or source versions, Edit opens a
  focused view of that section inside the panel. Catalogues can hold more than the 25
  options a select menu allows, so they are drawn as paged selects, which a modal cannot hold.

The manifest stores a size as three fields, such as capture_width, capture_height, and
capture_depth. The panel shows and edits each such triple as one "5 x 7 x 4" value; see
DimensionGroup.

Every save goes through SubmissionDraftService, which checks access and the draft revision
the user was looking at. Pressing Submit asks SubmissionFinalizationService to create the
build. That request can queue the build, wait for files to finish processing, or record
issues for the submitter to fix; the panel reloads after each change and shows which of
these happened.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from functools import partial
from typing import Any, cast, override
from uuid import UUID, uuid4

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.draft_intake import add_uploaded_files, receive_retained_file, retry_uploaded_file
from squid.bot.submission.parse import format_dimensions, parse_dimensions
from squid.bot.submission.ui.routes import build_edit
from squid.bot.ui import DISCORD_BLUE, DISCORD_GREEN, DISCORD_RED, DISCORD_YELLOW
from squid.builds.domain.models import parse_time_string
from squid.core.errors import JSONValue, SquidError
from squid.core.i18n import tr
from squid.runtime import BotServices
from squid.submissions.application import FinalizationJobSnapshot, StoredDraft
from squid.submissions.application.intake import SuppliedAttachment
from squid.submissions.domain import (
    ChoiceOption,
    DraftChange,
    DraftChangeKey,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
    FormField,
    FormManifest,
    FormSection,
    ValueKind,
)
from squid.submissions.domain.finalization import (
    FinalizationJobStatus,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)
from squid.submissions.domain.intake import IntakeStatus
from squid_i18n import NUMBER
from squid_reactivity import TopicHandle
from squid_ui.text import Message, TextLike, md
from squid_ui_discord.modal import FileField

FILES_FOCUS = "files"
"""The focus value that shows the files view instead of a form section."""

MAX_MODAL_FIELDS = 5
"""Discord modals hold at most five inputs."""

PICKER_PAGE_SIZE = 25
"""Discord select menus hold at most 25 options."""

MAX_FILES_PER_UPLOAD = 4
"""Files accepted by one Add files modal, matching the four attachment options of /build submit."""

_VALUE_PREVIEW_LENGTH = 100
"""Longer answers, such as descriptions, are shortened in the summary rows."""


@dataclass(frozen=True, slots=True)
class DimensionGroup:
    """Three manifest fields edited as one "width x height x depth" input.

    The input accepts what parse_dimensions accepts: two or three numbers separated by x or *,
    with ? for an unknown value. A missing or unknown part is saved as unset, so a required
    part shows as missing in the summary like any other required field.
    """

    key: str
    """The modal input key; it must not collide with a manifest field ID."""
    label: Message
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


type _Projection = tuple[StoredDraft, tuple[SuppliedAttachment, ...], FinalizationJobSnapshot | None]


class Phase(Enum):
    """Where a draft is in the submission lifecycle, as far as the panel is concerned."""

    EDITING = auto()
    """The submitter is filling in answers and has not submitted, or edited after submitting."""
    WAITING_FOR_FILES = auto()
    """Submitted, but finalization waits for attached files to finish processing."""
    NEEDS_CHANGES = auto()
    """Submitted, but finalization recorded issues the submitter must fix first."""
    CREATING = auto()
    """A finalization job is queued or running."""
    DONE = auto()
    """The build was created."""
    EXPIRED = auto()
    """The draft expired before it was submitted."""


_EDITABLE_PHASES = frozenset({Phase.EDITING, Phase.WAITING_FOR_FILES, Phase.NEEDS_CHANGES})
_PROCESSING_REASONS = frozenset(
    {SubmissionAttentionReason.SCHEMATIC_PROCESSING, SubmissionAttentionReason.MEDIA_PROCESSING}
)


class DraftEditorScreen(sd.Screen):
    """Edit and submit one saved draft using its pinned form manifest.

    The panel reloads when the draft's topic publishes a change, for example when a
    background worker finishes finalization, so it moves from "Creating" to "Done"
    without the submitter pressing anything. Refresh buttons remain as a fallback.
    """

    policy = sd.ResponseSpec(audience="personal", timeout=600)
    focus: str | None = sl.state(None)
    """The section ID or FILES_FOCUS shown on its own, or None for the overview."""
    picker_pages: Mapping[str, int] = sl.state({})
    """The page each catalogue field's select menu shows, keyed by field ID."""
    notice: sl.TextLike = sl.state("")
    """An error from the last action, shown until the next action replaces it."""
    option_sets: Mapping[str, tuple[ChoiceOption, ...]] = sl.state({}, opaque=True)
    """Catalogue options keyed by option source, used for labels and select menus."""
    selected_attachment: str | None = sl.state(None)

    def __init__(
        self, services: BotServices, actor_id: int, draft: StoredDraft, manifest: FormManifest, *, changes: TopicHandle
    ) -> None:
        self.services = services
        self.actor_id = actor_id
        self._draft_id = draft.snapshot.id
        self._changes = changes
        self._seed: _Projection | None = (draft, (), None)
        self.manifest = manifest

    # Draft state

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def projection(self) -> _Projection:
        """Refresh the private editor when durable submission reconciliation publishes a change."""
        self._changes.watch()
        seed, self._seed = self._seed, None
        return seed if seed is not None else await self._read()

    def _current(self) -> _Projection:
        if self._seed is not None:
            return self._seed
        state = self.projection.status
        if isinstance(state, sl.resources.Ready):
            return state.value
        if state.previous is not None:
            return state.previous.value
        message = "This draft has not loaded yet."
        raise sl.resources.ResourceNotReadyError(message)

    @property
    def draft(self) -> StoredDraft:
        return self._current()[0]

    @property
    def attachments(self) -> tuple[SuppliedAttachment, ...]:
        """Files supplied to the draft, excluding discarded ones."""
        return tuple(item for item in self._current()[1] if item.status is not IntakeStatus.DISCARDED)

    @property
    def result(self) -> FinalizationJobSnapshot | None:
        return self._current()[2]

    @property
    def answers(self) -> dict[str, JSONValue]:
        """Saved answers with the manifest's defaults filled in."""
        return self.manifest.apply_defaults(
            self.draft.snapshot.category, self.draft.snapshot.answers, origin=self.draft.origin
        )

    @property
    def fields(self) -> tuple[FormField, ...]:
        """Fields visible for the current answers and draft origin, in manifest order."""
        answers = self.answers
        return tuple(
            field
            for field in self.manifest.fields_for(self.draft.snapshot.category)
            if field.is_visible(answers, self.draft.origin)
        )

    @property
    def sections(self) -> tuple[FormSection, ...]:
        """Manifest sections reduced to their visible fields; sections with none are dropped."""
        visible = {field.id for field in self.fields}
        manifest_sections = (
            *self.manifest.common_sections,
            *self.manifest.category(self.draft.snapshot.category).sections,
        )
        sections = (
            FormSection(section.id, section.title, tuple(field for field in section.fields if field.id in visible))
            for section in manifest_sections
        )
        return tuple(section for section in sections if section.fields)

    @property
    def phase(self) -> Phase:
        status = self.draft.snapshot.status
        if status is DraftStatus.EXPIRED:
            return Phase.EXPIRED
        if status is DraftStatus.SUBMITTED:
            return Phase.DONE
        if status is DraftStatus.PROCESSING:
            return Phase.CREATING
        if status is DraftStatus.NEEDS_ATTENTION:
            # A retry time means finalization only waits for files and will continue on its own.
            return Phase.WAITING_FOR_FILES if self.draft.preparation_retry_at is not None else Phase.NEEDS_CHANGES
        return Phase.EDITING

    @property
    def issues(self) -> tuple[SubmissionAttentionIssue, ...]:
        """Problems recorded by the last submission, without duplicates.

        Preparation issues are stored on the draft. Issues found while creating the build,
        such as exhausted retries, are stored on the finalization job.
        """
        issues = list(self.draft.preparation_issues)
        result = self.result
        if result is not None and result.status in {FinalizationJobStatus.NEEDS_ATTENTION, FinalizationJobStatus.DEAD}:
            issues.extend(result.issues)
        return tuple(dict.fromkeys(issues))

    # Rendering

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        state = self.projection.status
        if self._seed is None and not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(tr(t"draft-editor-loading = Loading your draft…")),)
        phase = self.phase
        children: list[Any] = [self._header(phase), sd.v2.separator()]
        if phase in _EDITABLE_PHASES and self.focus == FILES_FOCUS:
            children.extend(self._files_view())
        elif phase in _EDITABLE_PHASES and (section := self._focused_section()) is not None:
            children.extend(self._section_view(section))
        elif phase in _EDITABLE_PHASES:
            children.extend(self._overview(phase))
        else:
            children.extend(self._summary(phase))
        accent = {
            Phase.EDITING: DISCORD_BLUE,
            Phase.WAITING_FOR_FILES: DISCORD_BLUE,
            Phase.NEEDS_CHANGES: DISCORD_YELLOW,
            Phase.CREATING: DISCORD_BLUE,
            Phase.DONE: DISCORD_GREEN,
            Phase.EXPIRED: DISCORD_RED,
        }[phase]
        return (sd.v2.panel(*children, accent=accent),)

    def _header(self, phase: Phase) -> Any:
        """Title, a sentence about the current phase, and any problems to fix."""
        category = self.manifest.category(self.draft.snapshot.category).label
        expires = self.draft.expires_at.to_stdlib()
        match phase:
            case Phase.CREATING:
                title = tr(t"draft-editor-title-creating = ## Submitting your build · {category}")
            case Phase.DONE:
                title = tr(t"draft-editor-title-done = ## Build submitted · {category}")
            case Phase.EXPIRED:
                title = tr(t"draft-editor-title-expired = ## Draft expired · {category}")
            case _:
                title = tr(t"draft-editor-title-editing = ## Submit a build · {category}")
        match phase:
            case Phase.EDITING:
                status = tr(t"""
                    draft-editor-status-editing =
                        Fill in each section, then press **Submit**. Your answers are saved as you go.
                        -# Reopen this draft with `/build drafts` until {expires}.
                    """)
            case Phase.WAITING_FOR_FILES:
                status = tr(
                    t"draft-editor-status-waiting = ⏳ **Submitted. Waiting for your files to finish processing.** "
                    t"The submission continues on its own once they are ready. "
                    t"If you edit anything now, press **Submit** again afterwards."
                )
            case Phase.NEEDS_CHANGES:
                status = tr(
                    t"draft-editor-status-needs-changes = ⚠️ **Your submission needs changes.** "
                    t"Fix the problems below, then press **Submit** again."
                )
            case Phase.CREATING:
                status = tr(
                    t"draft-editor-status-creating = ⏳ Creating your build. This message updates when it is done."
                )
            case Phase.DONE:
                result = self.result.result if self.result is not None else None
                status = (
                    tr(
                        t"draft-editor-status-done = ✅ Your build was saved as **#{(build_id_text := str(result.build_id))}** "  # noqa: F841 - captured text identifier
                        t"and is now pending review. You can still edit it while it waits."
                    )
                    if result is not None
                    else tr(t"draft-editor-status-done-unknown = ✅ Your build was saved and is now pending review.")
                )
            case Phase.EXPIRED:
                status = tr(
                    t"draft-editor-status-expired = This draft expired before it was submitted. "
                    t"Start a new one with `/build submit`; your answers are listed below for reference."
                )
        lines: list[TextLike] = [title, status]
        issues = [
            issue
            for issue in self.issues
            if phase is not Phase.WAITING_FOR_FILES or issue.reason not in _PROCESSING_REASONS
        ]
        if phase in {Phase.NEEDS_CHANGES, Phase.WAITING_FOR_FILES} and issues:
            labels = {field.id: field.label for field in self.manifest.fields_for(self.draft.snapshot.category)}
            lines.extend(Message.join("", ("- ", _issue_text(issue, labels))) for issue in issues)
        if self.notice:
            lines.append(Message.join("", ("⚠️ ", self.notice)))
        return sd.v2.text(Message.join("\n", lines))

    def _overview(self, phase: Phase) -> list[Any]:
        """One row per section and the Files row, then the Submit row."""
        children: list[Any] = [
            sd.v2.section(
                self._section_text(section),
                accessory=sd.v2.button(
                    tr(t"draft-editor-edit = Edit"),
                    partial(self._edit_section, section.id),
                    key=f"edit_{section.id}",
                ),
            )
            for section in self.sections
        ]
        children.append(
            sd.v2.section(
                self._files_text(),
                accessory=(
                    sd.v2.button(tr(t"draft-editor-edit = Edit"), self._open_files, key="edit_files")
                    if self.attachments
                    else sd.v2.button(tr(t"draft-editor-add-files = Add files"), self._add_files, key="add_files")
                ),
            )
        )
        children.append(sd.v2.separator())
        missing = len(self._missing_required())
        if phase is Phase.WAITING_FOR_FILES:
            children.append(
                sd.v2.section(
                    tr(t"draft-editor-waiting-hint = This message updates when your files are ready."),
                    accessory=sd.v2.button(tr(t"draft-editor-refresh = Refresh"), self._refresh, key="refresh"),
                )
            )
            return children
        footer = (
            tr(t"""
                draft-editor-required-left = {{ {NUMBER(missing)} ->
                    [one] ⚠️ **{NUMBER(missing)} required field is empty.** Fill it in to submit.
                   *[other] ⚠️ **{NUMBER(missing)} required fields are empty.** Fill them in to submit.
                }}
                """)
            if missing
            else tr(t"draft-editor-ready = All required fields are filled in. Your files are checked when you submit.")
        )
        children.append(
            sd.v2.section(
                footer,
                accessory=sd.v2.button(
                    tr(t"draft-editor-submit = Submit"),
                    self._submit,
                    key="submit",
                    style=sl.primitives.ActionStyle.SUCCESS,
                    disabled=missing > 0,
                ),
            )
        )
        return children

    def _summary(self, phase: Phase) -> list[Any]:
        """Read-only answers for phases where the draft can no longer be edited."""
        children: list[Any] = [sd.v2.text(self._section_text(section)) for section in self.sections]
        children.append(sd.v2.text(self._files_text()))
        if phase is Phase.CREATING:
            children.extend(
                (
                    sd.v2.separator(),
                    sd.v2.row(sd.v2.button(tr(t"draft-editor-refresh = Refresh"), self._refresh, key="refresh")),
                )
            )
        elif phase is Phase.DONE and self.result is not None and self.result.result is not None:
            children.extend(
                (
                    sd.v2.separator(),
                    sd.v2.row(
                        sd.v2.routed_button(
                            tr(t"draft-editor-edit-build = Edit build"),
                            build_edit.id(build_id=self.result.result.build_id),
                        )
                    ),
                )
            )
        return children

    def _section_view(self, section: FormSection) -> list[Any]:
        """Typed fields as modal rows and catalogue fields as paged select menus."""
        answers = self.answers
        children: list[Any] = [sd.v2.heading(md(t"{section.title}"), level=3)]
        children.extend(
            sd.v2.section(
                Message.join("\n", self._answer_lines(chunk.fields, answers, show_empty=True)),
                accessory=sd.v2.button(
                    tr(t"draft-editor-edit = Edit"), partial(self._edit_form, chunk.id), key=f"form_{chunk.id}"
                ),
            )
            for chunk in _modal_chunks(section)
        )
        for field in section.fields:
            if field.option_source is not None:
                children.extend(self._picker(field, answers.get(field.id)))
        children.extend(
            (
                sd.v2.separator(),
                sd.v2.row(
                    sd.v2.button(
                        tr(t"draft-editor-done = Done"),
                        self._close_focus,
                        key="done",
                        style=sl.primitives.ActionStyle.PRIMARY,
                    )
                ),
            )
        )
        return children

    def _picker(self, field: FormField, value: JSONValue) -> list[Any]:
        """A catalogue field's current choice and one page of its options."""
        options = self.option_sets.get(field.option_source or "", ())
        selected = _as_list(value)
        labels = {option.value: option.label for option in options}
        chosen = ", ".join(labels.get(item, item) for item in selected)
        name = (
            tr(t"draft-editor-picker-required = **{(label := field.label)}** · Required")  # noqa: F841 - captured Fluent binding
            if field.required
            else md(t"**{field.label}**")
        )
        lines: list[TextLike] = [
            name,
            md(t"{chosen}") if chosen else tr(t"draft-editor-nothing-chosen = *Nothing chosen yet.*"),
        ]
        if not options:
            lines.append(tr(t"draft-editor-no-options = No options are available right now."))
            return [sd.v2.text(Message.join("\n", lines))]
        pages = (len(options) - 1) // PICKER_PAGE_SIZE + 1
        page = self._picker_page(field, options)
        multiple = field.value_kind is ValueKind.STRING_LIST
        shown = options[page * PICKER_PAGE_SIZE : (page + 1) * PICKER_PAGE_SIZE]
        if pages > 1:
            page_number = page + 1
            lines.append(
                tr(
                    t"draft-editor-picker-pages-multiple = -# Page {page_number} of {pages}. "
                    t"Choices on other pages are kept."
                )
                if multiple
                else tr(t"draft-editor-picker-pages = -# Page {page_number} of {pages}.")
            )
        children: list[Any] = [
            sd.v2.text(Message.join("\n", lines)),
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
        selected = _as_list(self.draft.snapshot.answers.get(field.id))
        first = next((index for index, option in enumerate(options) if option.value in selected), 0)
        return first // PICKER_PAGE_SIZE

    def _files_view(self) -> list[Any]:
        """Every supplied file, with actions for the one the submitter selects."""
        files = self.attachments
        children: list[Any] = [
            sd.v2.heading(tr(t"draft-editor-files-heading = Files"), level=3),
            sd.v2.text(self._files_text(heading=False)),
        ]
        selected = next((item for item in files if str(item.id) == self.selected_attachment), None)
        if files:
            children.append(
                sd.v2.select(
                    *(sd.v2.option(item.filename[:100], str(item.id), default=item is selected) for item in files),
                    on_select=self._select_attachment,
                    key="attachment",
                    placeholder=tr(t"draft-editor-choose-file = Choose a file to replace or remove…"),
                )
            )
        if selected is not None:
            children.append(
                sd.v2.row(
                    selected.source is not None
                    and selected.status is IntakeStatus.PENDING
                    and sd.v2.button(
                        tr(t"draft-editor-use-file = Use this file"), self._use_attachment, key="use_attachment"
                    ),
                    sd.v2.button(
                        tr(t"draft-editor-replace-file = Replace"), self._retry_attachment, key="retry_attachment"
                    ),
                    sd.v2.button(
                        tr(t"draft-editor-remove-file = Remove"),
                        self._discard_attachment,
                        key="discard_attachment",
                        style=sl.primitives.ActionStyle.DANGER,
                    ),
                    selected.kind == "schematic"
                    and sd.v2.button(
                        tr(t"draft-editor-main-schematic = Use as main schematic"),
                        self._primary_attachment,
                        key="primary_attachment",
                    ),
                )
            )
        children.extend(
            (
                sd.v2.separator(),
                sd.v2.row(
                    sd.v2.button(tr(t"draft-editor-add-files = Add files"), self._add_files, key="add_files"),
                    sd.v2.button(
                        tr(t"draft-editor-done = Done"),
                        self._close_focus,
                        key="done",
                        style=sl.primitives.ActionStyle.PRIMARY,
                    ),
                ),
            )
        )
        return children

    def _section_text(self, section: FormSection) -> Message:
        """The section title followed by one line per answer or missing required field."""
        lines = self._answer_lines(section.fields, self.answers, show_empty=False)
        if not lines:
            lines = [tr(t"draft-editor-section-empty = *Optional. Nothing added yet.*")]
        return Message.join("\n", (md(t"**{section.title}**"), *lines))

    def _answer_lines(
        self, fields: Sequence[FormField], answers: Mapping[str, JSONValue], *, show_empty: bool
    ) -> list[TextLike]:
        """Format "Label: value" lines, flagging missing required fields and invalid values.

        Empty optional fields are skipped unless show_empty is set, as in a section's own view
        where the submitter needs to see every field they can fill in.
        """
        errors = self.manifest.validate_answers(self.draft.snapshot.category, answers, origin=self.draft.origin)
        groups = _dimension_groups(fields)
        grouped = {field_id for group in groups for field_id in group.field_ids}
        lines: list[TextLike] = []
        for field in fields:
            group = next((group for group in groups if group.field_ids[0] == field.id), None)
            if group is not None:
                lines.append(self._dimensions_line(group, answers, errors, show_empty=show_empty))
                continue
            if field.id in grouped:
                continue
            value = answers.get(field.id)
            label = md(t"{field.label}: ")
            if _is_empty(value):
                if field.required:
                    lines.append(label + tr(t"draft-editor-required = ⚠️ *Required*"))
                elif show_empty:
                    lines.append(label + tr(t"draft-editor-not-set = *Not set*"))
                continue
            line = label + self._value_text(field, value)
            if (reason := errors.get(field.id)) is not None:
                line = Message.join("", (line, " — ⚠️ ", _problem_text(reason)))
            lines.append(line)
        return lines

    def _dimensions_line(
        self, group: DimensionGroup, answers: Mapping[str, JSONValue], errors: Mapping[str, str], *, show_empty: bool
    ) -> TextLike:
        """Show a size as "Build size: 5 x 7 x ?", flagging missing or invalid parts."""
        values = tuple(answers.get(field_id) for field_id in group.field_ids)
        label = Message.join("", (group.label, ": "))
        reason = next((errors[field_id] for field_id in group.field_ids if field_id in errors), None)
        if all(value is None for value in values):
            if reason is not None:
                return label + tr(t"draft-editor-required = ⚠️ *Required*")
            return label + tr(t"draft-editor-not-set = *Not set*") if show_empty else label
        size = format_dimensions(tuple(value if isinstance(value, int) else None for value in values))
        line = label + md(t"{size}")
        return line if reason is None else Message.join("", (line, " — ⚠️ ", _problem_text(reason)))

    def _value_text(self, field: FormField, value: JSONValue) -> TextLike:
        """Show an answer the way the submitter chose it: option labels, Yes or No, and seconds."""
        if isinstance(value, bool):
            return tr(t"draft-editor-yes = Yes") if value else tr(t"draft-editor-no = No")
        labels = {option.value: option.label for option in field.options}
        if field.option_source is not None:
            labels = {option.value: option.label for option in self.option_sets.get(field.option_source, ())}
        if isinstance(value, list):
            joined = ", ".join(labels.get(str(item), str(item)) for item in value)
            return md(t"{joined}")
        if field.value_kind is ValueKind.GAME_TICKS and isinstance(value, int):
            return md(t"{_seconds(value)}")
        text = labels.get(str(value), str(value)).replace("\n", " ")
        if len(text) > _VALUE_PREVIEW_LENGTH:
            text = text[: _VALUE_PREVIEW_LENGTH - 1].rstrip() + "…"
        return md(t"{text}")

    def _files_text(self, *, heading: bool = True) -> Message:
        """List each file with its kind and intake status."""
        lines: list[TextLike] = [tr(t"draft-editor-files-title = **Files**")] if heading else []
        editable = self.phase in _EDITABLE_PHASES
        if not self.attachments:
            lines.append(
                tr(t"draft-editor-files-empty = *No files yet. Add your schematic and any images or videos.*")
                if editable
                else tr(t"draft-editor-files-none = *No files.*")
            )
        for item in self.attachments:
            kind = {
                "schematic": tr(t"draft-editor-file-kind-schematic = Schematic"),
                "image": tr(t"draft-editor-file-kind-image = Image"),
                "video": tr(t"draft-editor-file-kind-video = Video"),
            }.get(item.kind, tr(t"draft-editor-file-kind-unknown = Unsupported file type"))
            match item.status:
                case IntakeStatus.READY:
                    status = tr(t"draft-editor-file-received = Received")
                case IntakeStatus.FAILED if editable:
                    status = tr(t"draft-editor-file-failed = ⚠️ Upload failed. Replace or remove it.")
                case IntakeStatus.FAILED:
                    status = tr(t"draft-editor-file-failed-final = ⚠️ Upload failed")
                case _ if item.source is not None and editable:
                    status = tr(t"draft-editor-file-not-added = Not added yet. Choose it under Edit to use it.")
                case _ if item.source is not None:
                    status = tr(t"draft-editor-file-not-used = Not used")
                case _:
                    status = tr(t"draft-editor-file-uploading = Uploading…")
            # Escaped text, not a code span: Discord shows escape backslashes inside code spans literally.
            lines.append(Message.join(" · ", (md(t"**{item.filename}**"), kind, status)))
        return Message.join("\n", lines)

    def _focused_section(self) -> FormSection | None:
        return next((section for section in self.sections if section.id == self.focus), None)

    def _missing_required(self) -> tuple[str, ...]:
        """IDs of empty required fields, with a size reported once under its group key.

        The count matches what the summary flags: one "Build size" line rather than three fields.
        """
        answers = self.answers
        missing = [field.id for field in self.fields if field.required and _is_empty(answers.get(field.id))]
        for group in _dimension_groups(self.fields):
            if any(field_id in missing for field_id in group.field_ids):
                missing = [field_id for field_id in missing if field_id not in group.field_ids] + [group.key]
        return tuple(missing)

    # Section editing

    async def _edit_section(self, section_id: str, event: sl.PressEvent) -> None:
        """Open the section's modal, or its focused view if a modal cannot hold it."""
        section = next(section for section in self.sections if section.id == section_id)
        chunks = _modal_chunks(section)
        if len(chunks) == 1 and len(chunks[0].fields) == len(section.fields):
            await self._present_form(chunks[0], event)
            return
        self.notice = ""
        self.focus = section_id
        await self._load_options(section.fields)

    async def _edit_form(self, chunk_id: str, event: sl.PressEvent) -> None:
        chunk = next(chunk for section in self.sections for chunk in _modal_chunks(section) if chunk.id == chunk_id)
        await self._present_form(chunk, event)

    async def _present_form(self, chunk: FormSection, event: sl.PressEvent) -> None:
        """Show a modal for up to five typed fields and save them together."""
        # Save against the revision the modal showed, so a stale modal cannot overwrite a newer edit.
        base_revision = self.draft.snapshot.revision
        answers = self.answers

        groups = _dimension_groups(chunk.fields)
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
            await self._save_values(values, base_revision=base_revision)

        entries: list[sl.forms.FormField[Any]] = []
        for field in chunk.fields:
            group = next((group for group in groups if group.field_ids[0] == field.id), None)
            if group is not None:
                entries.append(_dimensions_entry(group, chunk.fields, answers))
            elif field.id not in grouped:
                entries.append(_form_entry(field, answers.get(field.id)))
        await event.present_form(
            sl.forms.FormSpec(chunk.title[:45], tuple(entries)),
            key=f"draft-{chunk.id}",
            on_submit=submitted,
        )

    async def _select_value(self, field_id: str, event: sl.SelectionEvent) -> None:
        field = next(field for field in self.fields if field.id == field_id)
        value: JSONValue
        if field.value_kind is ValueKind.STRING_LIST:
            options = self.option_sets.get(field.option_source or "", ())
            page = self._picker_page(field, options)
            visible = {option.value for option in options[page * PICKER_PAGE_SIZE : (page + 1) * PICKER_PAGE_SIZE]}
            # A select menu reports only its own page; keep the choices made on other pages.
            retained = [item for item in _as_list(self.draft.snapshot.answers.get(field.id)) if item not in visible]
            value = [*retained, *event.values]
        else:
            value = event.values[0] if event.values else None
        await self._save_values({field.id: value})

    async def _turn_page(self, field_id: str, current: int, step: int, event: sl.PressEvent) -> None:
        self.picker_pages = {**self.picker_pages, field_id: max(0, current + step)}

    async def _close_focus(self, event: sl.PressEvent) -> None:
        self.focus = None
        self.notice = ""

    async def _load_options(self, fields: Iterable[FormField]) -> None:
        """Fetch the current catalogue for each catalogue field, replacing any earlier copy."""
        sources = {field.option_source for field in fields if field.option_source is not None}
        loaded = dict(self.option_sets)
        for source in sources:
            loaded[source] = (
                await self.services.submission_forms.options(source, self.draft.snapshot.category, locale=None)
            ).options
        self.option_sets = loaded

    async def _save_values(self, values: Mapping[str, JSONValue], *, base_revision: int | None = None) -> None:
        """Save several answers as one change; a failure saves none of them."""
        change = DraftChange(
            self.draft.snapshot.revision if base_revision is None else base_revision,
            "discord-editor",
            DraftChangeKey(str(uuid4())),
            tuple(
                FieldOperation(
                    uuid4(), field_id, FieldOperationKind.UNSET if value is None else FieldOperationKind.SET, value
                )
                for field_id, value in values.items()
            ),
        )
        try:
            await self.services.submission_drafts.apply_change(
                self.draft.snapshot.id, self.actor_id, change, locale=None
            )
        except SquidError as error:
            self.notice = error.public_text()
        else:
            self.notice = ""
        await self._reload()

    # Files

    async def _open_files(self, event: sl.PressEvent) -> None:
        self.notice = ""
        self.focus = FILES_FOCUS

    async def _select_attachment(self, event: sl.SelectionEvent) -> None:
        self.selected_attachment = event.values[0] if event.values else None

    async def _add_files(self, event: sl.PressEvent) -> None:
        async def supplied(event: sl.SubmitEvent) -> None:
            uploaded = event.values.get("files")
            files = uploaded if isinstance(uploaded, tuple) else (uploaded,) if uploaded is not None else ()
            failed = await add_uploaded_files(
                self.services,
                self.draft.snapshot.id,
                self.actor_id,
                tuple(item for item in files if isinstance(item, sl.forms.UploadedFile)),
            )
            self.notice = (
                tr(
                    t"draft-editor-files-failed = {failed} of the files could not be added. "
                    t"They are listed under Files so you can replace or remove them."
                )
                if failed
                else ""
            )
            await self._reload()

        await event.present_form(
            sl.forms.FormSpec(
                tr(t"draft-editor-add-files = Add files"),
                (
                    FileField(
                        key="files",
                        label=tr(t"draft-editor-files-label = Schematic, images, or videos"),
                        minimum=1,
                        maximum=MAX_FILES_PER_UPLOAD,
                    ),
                ),
            ),
            key="add-files",
            on_submit=supplied,
        )

    async def _discard_attachment(self, event: sl.PressEvent) -> None:
        if self.selected_attachment is not None:
            await self.services.submission_intake.discard(
                self.draft.snapshot.id, self.actor_id, UUID(self.selected_attachment)
            )
            self.selected_attachment = None
            await self._reload()

    async def _use_attachment(self, event: sl.PressEvent) -> None:
        if self.selected_attachment is None:
            return
        await event.acknowledge()
        try:
            await receive_retained_file(
                self.services, self.draft.snapshot.id, self.actor_id, UUID(self.selected_attachment)
            )
        except Exception:
            self.notice = tr(
                t"draft-editor-file-download-failed = The file could not be downloaded. Replace or remove it."
            )
        await self._reload()

    async def _primary_attachment(self, event: sl.PressEvent) -> None:
        if self.selected_attachment is not None:
            await self.services.submission_schematics.select_primary(
                self.draft.snapshot.id, self.actor_id, UUID(self.selected_attachment)
            )
            await self._reload()

    async def _retry_attachment(self, event: sl.PressEvent) -> None:
        if self.selected_attachment is None:
            return
        original_id = UUID(self.selected_attachment)

        async def supplied(event: sl.SubmitEvent) -> None:
            source = event.values.get("file")
            if not isinstance(source, sl.forms.UploadedFile):
                return
            try:
                new_id = await retry_uploaded_file(
                    self.services, self.draft.snapshot.id, self.actor_id, original_id, source
                )
            except Exception:
                self.notice = tr(
                    t"draft-editor-replacement-failed = The replacement could not be added. Try again or remove it."
                )
            else:
                self.selected_attachment = str(new_id)
                self.notice = ""
            await self._reload()

        await event.present_form(
            sl.forms.FormSpec(
                tr(t"draft-editor-replace-title = Replace file"),
                (FileField(key="file", label=tr(t"draft-editor-replacement-label = New file"), minimum=1, maximum=1),),
            ),
            key="retry-file",
            on_submit=supplied,
        )

    # Lifecycle

    async def _read(self) -> _Projection:
        draft = await self.services.submission_drafts.get_accessible(self._draft_id, self.actor_id)
        attachments = await self.services.submission_intake.list(self._draft_id, self.actor_id)
        result = await self.services.submission_finalization.status(self._draft_id, self.actor_id)
        return draft, attachments, result if isinstance(result, FinalizationJobSnapshot) else None

    async def _reload(self) -> None:
        current = await self._read()
        if self._seed is not None:
            self._seed = current
        else:
            self.projection.replace(current)
        if not any(str(item.id) == self.selected_attachment for item in self.attachments):
            self.selected_attachment = None

    async def _refresh(self, event: sl.PressEvent) -> None:
        await self._reload()

    async def _submit(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        try:
            await self.services.submission_finalization.submit(self.draft.snapshot.id, self.actor_id, locale=None)
        except SquidError as error:
            self.notice = error.public_text()
        else:
            self.notice = ""
            self.focus = None
        await self._reload()


@dataclass(frozen=True, slots=True)
class _TicksField(sl.forms.DurationField[Any]):
    """A timing entered as game ticks, redstone ticks, or seconds and stored as game ticks."""

    @override
    def format(self, value: object) -> sl.forms.PrefillValue:
        return _seconds(value) if isinstance(value, int) and not isinstance(value, bool) else None


_BARE_NUMBER = re.compile(r"[~≈]?\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)")


def _parse_ticks(text: str) -> int:
    """Parse "30gt", "15rt", or "1.5s" into game ticks, insisting on a unit.

    parse_time_string reads a bare number as seconds, but submitters often mean ticks,
    so the form asks for the unit instead of guessing. The manifest's help text says so too.
    """
    if _BARE_NUMBER.fullmatch(text.strip()):
        message = "Add a unit: gt for game ticks, rt for redstone ticks, or s for seconds."
        raise ValueError(message)
    ticks = parse_time_string(text)
    if ticks is None or ticks < 0:
        message = "Enter a time such as 30gt, 15rt, or 1.5s."
        raise ValueError(message)
    return ticks


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
            parser=_parse_ticks,
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


def _dimension_groups(fields: Sequence[FormField]) -> tuple[DimensionGroup, ...]:
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


def _modal_chunks(section: FormSection) -> tuple[FormSection, ...]:
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


def _problem_text(reason: str) -> Message:
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


_FIELD_REASONS = frozenset(
    {
        SubmissionAttentionReason.REQUIRED,
        SubmissionAttentionReason.WRONG_TYPE,
        SubmissionAttentionReason.REQUIRED_VALUE,
        SubmissionAttentionReason.BELOW_MINIMUM,
        SubmissionAttentionReason.ABOVE_MAXIMUM,
        SubmissionAttentionReason.TOO_SHORT,
        SubmissionAttentionReason.TOO_LONG,
        SubmissionAttentionReason.TOO_FEW_ITEMS,
        SubmissionAttentionReason.TOO_MANY_ITEMS,
        SubmissionAttentionReason.UNKNOWN_OPTION,
        SubmissionAttentionReason.UNKNOWN_FIELD,
    }
)


def _issue_text(issue: SubmissionAttentionIssue, labels: Mapping[str, str]) -> TextLike:
    """Explain one submission issue as a line the submitter can act on.

    Field problems read "Label: problem". Issues about files or the submission as a whole
    have no field label, so they get a full sentence instead.
    """
    if issue.reason in _FIELD_REASONS:
        label = labels.get(issue.field_id, issue.field_id)
        return md(t"{label}: ") + _problem_text(issue.reason)
    match issue.reason:
        case SubmissionAttentionReason.SCHEMA_UNSUPPORTED:
            return tr(
                t"draft-editor-issue-schema-unsupported = This draft uses an old version of the form. "
                t"Start a new one with `/build submit`."
            )
        case SubmissionAttentionReason.SCHEMATIC_REQUIRED:
            return tr(t"draft-editor-issue-schematic-required = Add a schematic under **Files**.")
        case SubmissionAttentionReason.SCHEMATIC_PROCESSING:
            return tr(t"draft-editor-issue-schematic-processing = Your schematic is still being processed.")
        case SubmissionAttentionReason.SCHEMATIC_REJECTED:
            return tr(
                t"draft-editor-issue-schematic-rejected = Your schematic could not be read. Replace it under **Files**."
            )
        case SubmissionAttentionReason.SCHEMATIC_PRIMARY_REQUIRED:
            return tr(
                t"draft-editor-issue-schematic-primary = You added several schematics. "
                t"Choose the main one under **Files**."
            )
        case SubmissionAttentionReason.MEDIA_PROCESSING:
            return tr(t"draft-editor-issue-media-processing = Your images or videos are still being processed.")
        case SubmissionAttentionReason.MEDIA_REJECTED:
            return tr(
                t"draft-editor-issue-media-rejected = A file could not be used. Replace or remove it under **Files**."
            )
        case SubmissionAttentionReason.SPONSOR_UNAVAILABLE:
            return tr(
                t"draft-editor-issue-sponsor = The Minecraft server credited for this build is no longer available "
                t"for credit. Turn off server credit to continue."
            )
        case SubmissionAttentionReason.RETRY_EXHAUSTED:
            return tr(
                t"draft-editor-issue-retry-exhausted = Creating the build failed several times. "
                t"Press **Submit** to try again, or ask staff for help if it keeps failing."
            )
        case SubmissionAttentionReason.PERMISSION_REVOKED:
            return tr(t"draft-editor-issue-permission = You no longer have permission to submit this draft.")
        case _:
            return tr(
                t"draft-editor-issue-target-rejected = The build could not be created from these answers. "
                t"Check them and press **Submit** again."
            )


def _seconds(ticks: int) -> str:
    """Format game ticks as exact seconds, such as 30 as "1.5s", matching build cards."""
    return f"{(Decimal(ticks) / 20).normalize():f}s"


def _as_list(value: JSONValue) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [] if value is None else [str(value)]


def _is_empty(value: JSONValue) -> bool:
    return value is None or value == [] or value == ""


async def draft_editor(
    services: BotServices, actor_id: int, draft_id: UUID, *, changes: TopicHandle, notice: sl.TextLike = ""
) -> DraftEditorScreen:
    """Open a saved draft with the form revision it was created under.

    Catalogue options are loaded up front so the summary can show their labels.

    Args:
        notice: Text shown at the top of the panel until the submitter's next action, such as
            command options that could not be used.

    Raises:
        ValueError: This server can no longer read the draft's form revision.
    """
    draft = await services.submission_drafts.get_accessible(draft_id, actor_id)
    manifest = await services.submission_forms.manifest_revision(
        draft.snapshot.schema_id, draft.snapshot.schema_revision, locale=None
    )
    if manifest is None:
        message = "This draft's form revision is unavailable."
        raise ValueError(message)
    screen = DraftEditorScreen(services, actor_id, draft, manifest, changes=changes)
    await screen._reload()
    await screen._load_options(screen.fields)
    screen.notice = notice
    return screen
