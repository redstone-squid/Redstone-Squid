"""The private Discord panel for filling in and submitting a build draft.

A submission draft stores answers keyed by the field IDs of a pinned FormManifest. The
manifest groups fields into sections such as "Name and creators" or "Door size and type".
The draft panel (DraftEditorScreen) is one message: a row per section listing every answer
in it with an Edit button beside the row, a Files row, and the Submit button.

Editing never replaces that overview:

- A section whose fields all fit a Discord modal (at most five typed fields) opens the
  modal directly.
- A section with catalogue fields, such as patterns or tags, opens a separate private
  message (SectionEditorScreen) with paged selects. Catalogues can hold more than the 25
  options a select menu allows, which a modal cannot hold.
- Files open in their own message too (FilesEditorScreen), and Preview opens a message
  showing the build card other people will see (BuildPreviewScreen).

All of these messages read one DraftWorkspace, so a change made in any of them redraws the
others. The manifest stores a size as three fields, such as capture_width, capture_height,
and capture_depth; the panel shows and edits each triple as one "5 x 7 x 4" value (see
DimensionGroup).

Every save goes through SubmissionDraftService, which checks access and the draft revision
the user was looking at. Pressing Submit asks SubmissionFinalizationService to create the
build. That request can queue the build, wait for files to finish processing, or record
issues for the submitter to fix; the panel shows which of these happened.
"""

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from functools import partial
from typing import Any, cast, override
from uuid import UUID

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.edit_history import history_controls, save_edit
from squid.bot.submission import draft_intake
from squid.bot.submission.parse import format_dimensions, parse_dimensions
from squid.bot.submission.ui import controls as build_controls
from squid.bot.submission.ui.editor import BuildEditorScreen, EditorSection
from squid.bot.ui import DISCORD_BLUE, DISCORD_GREEN, DISCORD_RED, DISCORD_YELLOW
from squid.builds.application.input import parse_build_ticks
from squid.builds.models import Build
from squid.core.errors import JSONValue, SquidError
from squid.core.i18n import tr
from squid.runtime import BotServices
from squid.submissions.application import FinalizationJobSnapshot, StoredDraft
from squid.submissions.application.intake import SuppliedAttachment
from squid.submissions.domain import (
    ChoiceOption,
    DraftStatus,
    FormField,
    FormManifest,
    FormSection,
    ValueKind,
)
from squid.submissions.domain.drafts import DraftRevisionConflictError
from squid.submissions.domain.finalization import (
    FinalizationJobStatus,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)
from squid.submissions.domain.intake import IntakeStatus
from squid.submissions.infrastructure.build_target import PREVIEW_TITLE_FIELDS, preview_build
from squid_i18n import NUMBER
from squid_reactivity import TopicHandle
from squid_ui.interactions import PressHandler
from squid_ui.text import Message, TextLike, md
from squid_ui_discord.modal import FileField

MAX_MODAL_FIELDS = 5
"""Discord modals hold at most five inputs."""

PICKER_PAGE_SIZE = 25
"""Discord select menus hold at most 25 options."""

MAX_FILES_PER_UPLOAD = 4
"""Files accepted by one Add files modal, matching the four file options of /build submit."""

_VALUE_PREVIEW_LENGTH = 100
"""Longer answers, such as descriptions, are shortened in the summary rows."""

type CardRenderer = Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]]
"""Draws a build card exactly as posted builds are drawn; it needs the bot, which screens do not hold."""


@dataclass(frozen=True, slots=True)
class DimensionGroup:
    """Three manifest fields edited as one "width x height x depth" input.

    The input accepts what parse_dimensions accepts: two or three numbers separated by x or *,
    with ? for an unknown value. A missing or unknown part is saved as unset; if the form
    requires that part, the summary flags it like any other required field.
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


EDITABLE_PHASES = frozenset({Phase.EDITING, Phase.WAITING_FOR_FILES, Phase.NEEDS_CHANGES})
_PROCESSING_REASONS = frozenset(
    {SubmissionAttentionReason.SCHEMATIC_PROCESSING, SubmissionAttentionReason.MEDIA_PROCESSING}
)


class DraftWorkspace(sl.Reactive):
    """One open draft, shared by the panel and the editor and preview messages it opens.

    The workspace loads the draft, its files, and its finalization result, and reloads them
    when the draft's topic publishes a change, for example when a background worker finishes
    the build. Every message that reads it redraws after a save, whichever message made it.
    """

    history: sl.runtime.History = sl.runtime.history(limit=20)

    notice: sl.TextLike = sl.state("")
    """An error from the last action, shown until the next action replaces it."""
    option_sets: Mapping[str, tuple[ChoiceOption, ...]] = sl.state({}, opaque=True)
    """Catalogue options keyed by option source, used for labels and select menus."""

    def __init__(
        self,
        services: BotServices,
        actor_id: int,
        draft: StoredDraft,
        manifest: FormManifest,
        *,
        changes: TopicHandle,
        render_card: CardRenderer | None = None,
    ) -> None:
        self.services = services
        self.actor_id = actor_id
        self.manifest = manifest
        self.render_card = render_card
        self._draft_id = draft.snapshot.id
        self._changes = changes
        self._seed: _Projection | None = (draft, (), None)

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def projection(self) -> _Projection:
        """Reload the draft when durable submission reconciliation publishes a change."""
        self._changes.watch()
        seed, self._seed = self._seed, None
        return seed if seed is not None else await self._read()

    @property
    def loaded(self) -> bool:
        state = self.projection.status
        return self._seed is not None or isinstance(state, sl.resources.Ready) or state.previous is not None

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
    def category(self) -> str:
        return self.draft.snapshot.category

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
        return self.manifest.apply_defaults(self.category, self.draft.snapshot.answers, origin=self.draft.origin)

    @property
    def fields(self) -> tuple[FormField, ...]:
        """Fields visible for the current answers and draft origin, in manifest order."""
        answers = self.answers
        return tuple(
            field for field in self.manifest.fields_for(self.category) if field.is_visible(answers, self.draft.origin)
        )

    @property
    def sections(self) -> tuple[FormSection, ...]:
        """Manifest sections reduced to their visible fields; sections with none are dropped."""
        visible = {field.id for field in self.fields}
        manifest_sections = (*self.manifest.common_sections, *self.manifest.category(self.category).sections)
        sections = (
            FormSection(section.id, section.title, tuple(field for field in section.fields if field.id in visible))
            for section in manifest_sections
        )
        return tuple(section for section in sections if section.fields)

    def section(self, section_id: str) -> FormSection | None:
        return next((section for section in self.sections if section.id == section_id), None)

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

    def missing_required(self) -> tuple[str, ...]:
        """IDs of empty required fields, with a size reported once under its group key.

        The count matches what the summary flags: one "Door size" line rather than its parts.
        """
        answers = self.answers
        missing = [field.id for field in self.fields if field.required and _is_empty(answers.get(field.id))]
        for group in _dimension_groups(self.fields):
            if any(field_id in missing for field_id in group.field_ids):
                missing = [field_id for field_id in missing if field_id not in group.field_ids] + [group.key]
        return tuple(missing)

    async def reload(self) -> None:
        current = await self._read()
        if self._seed is not None:
            self._seed = current
        else:
            self.projection.replace(current)

    async def _read(self) -> _Projection:
        draft = await self.services.submission_drafts.get_accessible(self._draft_id, self.actor_id)
        attachments = await self.services.submission_intake.list(self._draft_id, self.actor_id)
        result = await self.services.submission_finalization.status(self._draft_id, self.actor_id)
        return draft, attachments, result if isinstance(result, FinalizationJobSnapshot) else None

    async def load_options(self, fields: Iterable[FormField]) -> None:
        """Fetch the current catalogue for each catalogue field, replacing any earlier copy."""
        sources = {field.option_source for field in fields if field.option_source is not None}
        loaded = dict(self.option_sets)
        for source in sources:
            loaded[source] = (await self.services.submission_forms.options(source, self.category, locale=None)).options
        self.option_sets = loaded

    async def save_values(self, values: Mapping[str, JSONValue], *, base_revision: int | None = None) -> None:
        """Save several answers as one change; a failure saves none of them and sets the notice."""
        before = {key: self.draft.snapshot.answers.get(key) for key in values}

        async def replace_values(expected: Mapping[str, JSONValue], replacement: Mapping[str, JSONValue]) -> bool:
            return await self.services.submission_drafts.replace_answers(
                self._draft_id, self.actor_id, expected, replacement
            )

        async def write() -> bool:
            return await self.services.submission_drafts.replace_answers(
                self._draft_id, self.actor_id, before, values, base_revision=base_revision
            )

        # A modal also guards the revision it displayed, even when the changed field stayed equal.
        if base_revision is not None and base_revision != self.draft.snapshot.revision:
            self.notice = DraftRevisionConflictError(
                expected=base_revision, actual=self.draft.snapshot.revision
            ).public_text()
            await self.reload()
            return
        try:
            await save_edit(
                self.history,
                tr(t"draft-history-answers = Edit submission answers"),
                before,
                dict(values),
                target=f"draft:{self._draft_id}:answers",
                authorize=self.may_edit,
                replace=replace_values,
                refresh=self.reload,
                write=write,
            )
        except SquidError as error:
            self.notice = error.public_text()
        else:
            self.notice = ""

    async def may_edit(self) -> bool:
        try:
            current = await self.services.submission_drafts.get_accessible(self._draft_id, self.actor_id)
        except SquidError:
            return False
        return current.snapshot.status in {DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION}

    async def submit(self) -> None:
        try:
            await self.services.submission_finalization.submit(self._draft_id, self.actor_id, locale=None)
        except SquidError as error:
            self.notice = error.public_text()
        else:
            self.notice = ""
            self.history.clear()
        await self.reload()

    async def add_files(self, files: Sequence[sl.forms.UploadedFile]) -> None:
        failed = await draft_intake.add_uploaded_files(self.services, self._draft_id, self.actor_id, files)
        self.notice = (
            tr(
                t"draft-editor-files-failed = {failed} of the files could not be added. "
                t"They are listed under Files so you can replace or remove them."
            )
            if failed
            else ""
        )
        await self.reload()

    async def remove_file(self, file_id: UUID) -> None:
        await self.services.submission_intake.discard(self._draft_id, self.actor_id, file_id)
        await self.reload()

    async def use_file(self, file_id: UUID) -> None:
        """Download a file found in the build-log post the draft was inferred from."""
        try:
            await draft_intake.receive_retained_file(self.services, self._draft_id, self.actor_id, file_id)
        except Exception:
            self.notice = tr(
                t"draft-editor-file-download-failed = The file could not be downloaded. Replace or remove it."
            )
        await self.reload()

    async def make_main_schematic(self, file_id: UUID) -> None:
        await self.services.submission_schematics.select_primary(self._draft_id, self.actor_id, file_id)
        await self.reload()

    async def replace_file(self, file_id: UUID, source: sl.forms.UploadedFile) -> UUID | None:
        """Add a replacement, then remove the original; on failure keep the original and set the notice."""
        try:
            new_id = await draft_intake.retry_uploaded_file(
                self.services, self._draft_id, self.actor_id, file_id, source
            )
        except Exception:
            self.notice = tr(
                t"draft-editor-replacement-failed = The replacement could not be added. Try again or remove it."
            )
            new_id = None
        else:
            self.notice = ""
        await self.reload()
        return new_id


class DraftEditorScreen(BuildEditorScreen[None]):
    """The draft panel: an overview of every section, then Submit.

    Before submission it lists answers with Edit buttons. Afterwards it shows progress and a
    read-only summary, ending with a link to edit the created build.
    """

    # live_updates redraws the panel for changes made elsewhere: editor messages, or finalization
    # through the draft's topic. retain_routed_on_timeout keeps Reopen usable after it expires.
    policy = sd.ResponseSpec(audience="personal", timeout=600, live_updates=True, retain_routed_on_timeout=True)

    def __init__(self, workspace: DraftWorkspace) -> None:
        self.workspace = workspace

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        ws = self.workspace
        if self.expired:
            # The message can no longer be edited, so replace progress that would go stale.
            return (
                sl.status(tr(t"draft-editor-expired = This panel has closed. Your answers are saved.")),
                sl.action_controls(
                    sl.routed_action_control(
                        tr(t"draft-editor-reopen = Reopen draft"),
                        build_controls.reopen_draft.id(draft_id=str(ws.draft.snapshot.id)),
                        key="reopen",
                    ),
                    key="draft-reopen",
                ),
            )
        if not ws.loaded:
            return (sl.status(tr(t"draft-editor-loading = Loading your draft…")),)
        phase = ws.phase
        children: list[Any] = [_header(ws, phase), sd.v2.separator()]
        children.extend(self._overview(phase) if phase in EDITABLE_PHASES else self._summary(phase))
        accent = {
            Phase.EDITING: DISCORD_BLUE,
            Phase.WAITING_FOR_FILES: DISCORD_BLUE,
            Phase.NEEDS_CHANGES: DISCORD_YELLOW,
            Phase.CREATING: DISCORD_BLUE,
            Phase.DONE: DISCORD_GREEN,
            Phase.EXPIRED: DISCORD_RED,
        }[phase]
        return (sd.v2.panel(*children, accent=accent), *history_controls(ws.history))

    def _overview(self, phase: Phase) -> list[Any]:
        """One row per section and the Files row, then the Submit row."""
        ws = self.workspace
        children = self.section_overview(
            [EditorSection(section.id, _section_text(ws, section)) for section in ws.sections]
        )
        children.append(
            sd.v2.section(
                _files_text(ws),
                accessory=(
                    sd.v2.button(tr(t"draft-editor-edit = Edit"), self._open_files, key="edit_files")
                    if ws.attachments
                    else sd.v2.button(tr(t"draft-editor-add-files = Add files"), self._add_files, key="add_files")
                ),
            )
        )
        children.append(sd.v2.separator())
        preview = ws.render_card is not None and sd.v2.button(
            tr(t"draft-editor-preview = Preview"), self._preview, key="preview"
        )
        if phase is Phase.WAITING_FOR_FILES:
            children.extend(
                (
                    sd.v2.text(tr(t"draft-editor-waiting-hint = This message updates when your files are ready.")),
                    sd.v2.row(
                        preview, sd.v2.button(tr(t"draft-editor-refresh = Refresh"), self._refresh, key="refresh")
                    ),
                )
            )
            return children
        missing = len(ws.missing_required())
        footer = (
            tr(t"""
                draft-editor-required-left = {{ {NUMBER(missing)} ->
                    [one] ⚠️ **{NUMBER(missing)} required field is empty.** Fill it in to submit.
                   *[other] ⚠️ **{NUMBER(missing)} required fields are empty.** Fill them in to submit.
                }}
                """)
            if missing
            else tr(t"draft-editor-ready = Ready to submit.")
        )
        children.extend(
            (
                sd.v2.text(footer),
                sd.v2.row(
                    preview,
                    sd.v2.button(
                        tr(t"draft-editor-submit = Submit"),
                        self._submit,
                        key="submit",
                        style=sl.primitives.ActionStyle.SUCCESS,
                        disabled=missing > 0,
                    ),
                ),
            )
        )
        return children

    def _summary(self, phase: Phase) -> list[Any]:
        """Read-only answers for phases where the draft can no longer be edited."""
        ws = self.workspace
        children: list[Any] = [sd.v2.text(_section_text(ws, section)) for section in ws.sections]
        children.append(sd.v2.text(_files_text(ws)))
        if phase is Phase.CREATING:
            children.extend(
                (
                    sd.v2.separator(),
                    sd.v2.row(sd.v2.button(tr(t"draft-editor-refresh = Refresh"), self._refresh, key="refresh")),
                )
            )
        elif phase is Phase.DONE and ws.result is not None and ws.result.result is not None:
            children.extend(
                (
                    sd.v2.separator(),
                    sd.v2.row(
                        sd.v2.routed_button(
                            tr(t"draft-editor-edit-build = Edit build"),
                            build_controls.edit_build.id(build_id=ws.result.result.build_id),
                        )
                    ),
                )
            )
        return children

    async def _edit_section(self, section_id: str, event: sd.PressEvent) -> None:
        """Open the section's modal, or its own editor message if a modal cannot hold it."""
        section = self.workspace.section(section_id)
        if section is None:
            return
        chunks = _modal_chunks(section)
        if len(chunks) == 1 and len(chunks[0].fields) == len(section.fields):
            await present_chunk_form(self.workspace, chunks[0], event)
            return
        await self.workspace.load_options(section.fields)
        await self.open_child(event, SectionEditorScreen(self.workspace, section_id), on_result=self._child_closed)

    async def _open_files(self, event: sd.PressEvent) -> None:
        await self.open_child(event, FilesEditorScreen(self.workspace), on_result=self._child_closed)

    async def _add_files(self, event: sd.PressEvent) -> None:
        await present_add_files(self.workspace, event)

    async def _preview(self, event: sd.PressEvent) -> None:
        await self.open_child(event, BuildPreviewScreen(self.workspace), on_result=self._child_closed)

    async def _refresh(self, event: sd.PressEvent) -> None:
        await self.workspace.reload()

    async def _submit(self, event: sd.PressEvent) -> None:
        await event.acknowledge()
        await self.workspace.submit()


_DETAIL_SESSION = sd.SessionSpec("draft-detail")
"""Section and file editors share one session, so opening one closes the editor message already open."""


class SectionEditorScreen(sd.Screen[None]):
    """A private message for one section whose catalogue fields need paged selects.

    Typed fields in the section still use modals, from Edit buttons beside their values.
    Choices save immediately; Done only closes this message.
    """

    policy = sd.ResponseSpec(audience="personal", timeout=600, live_updates=True, session=_DETAIL_SESSION)
    picker_pages: Mapping[str, int] = sl.state({})
    """The page each catalogue field's select menu shows, keyed by field ID."""

    def __init__(self, workspace: DraftWorkspace, section_id: str) -> None:
        self.workspace = workspace
        self.section_id = section_id

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        ws = self.workspace
        section = ws.section(self.section_id)
        if (closed := _closed_detail(self, ws)) is not None:
            return closed
        if section is None:
            return (sl.status(tr(t"draft-editor-section-gone = This section is no longer part of the form.")),)
        answers = ws.answers
        children: list[Any] = [sd.v2.heading(md(t"{section.title}"))]
        if ws.notice:
            children.append(sd.v2.text(Message.join("", ("⚠️ ", ws.notice))))
        # Follow the form's field order: a picker where its field is, and each modal group where it starts.
        chunks = {chunk.fields[0].id: chunk for chunk in _modal_chunks(section)}
        for field in section.fields:
            if field.option_source is not None:
                children.extend(self._picker(field, answers.get(field.id)))
            elif (chunk := chunks.get(field.id)) is not None:
                children.append(
                    sd.v2.section(
                        Message.join("\n", _answer_lines(ws, chunk.fields, answers, show_empty=True)),
                        accessory=sd.v2.button(
                            tr(t"draft-editor-edit = Edit"), partial(self._edit_form, chunk.id), key=f"form_{chunk.id}"
                        ),
                    )
                )
        children.extend((sd.v2.separator(), _done_row(self._done)))
        return (sd.v2.panel(*children, accent=DISCORD_BLUE), *history_controls(self.workspace.history))

    def _picker(self, field: FormField, value: JSONValue) -> list[Any]:
        """A catalogue field's current choice and one page of its options."""
        options = self.workspace.option_sets.get(field.option_source or "", ())
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
        selected = _as_list(self.workspace.draft.snapshot.answers.get(field.id))
        first = next((index for index, option in enumerate(options) if option.value in selected), 0)
        return first // PICKER_PAGE_SIZE

    async def _edit_form(self, chunk_id: str, event: sd.PressEvent) -> None:
        section = self.workspace.section(self.section_id)
        chunk = next((chunk for chunk in _modal_chunks(section) if chunk.id == chunk_id), None) if section else None
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
            retained = [item for item in _as_list(ws.draft.snapshot.answers.get(field.id)) if item not in visible]
            value = [*retained, *event.values]
        else:
            value = event.values[0] if event.values else None
        await ws.save_values({field.id: value})

    async def _turn_page(self, field_id: str, current: int, step: int, event: sd.PressEvent) -> None:
        self.picker_pages = {**self.picker_pages, field_id: max(0, current + step)}

    async def _done(self, event: sd.PressEvent) -> None:
        self.complete(event, None)


class FilesEditorScreen(sd.Screen[None]):
    """A private message for adding, replacing, and removing the draft's files."""

    policy = sd.ResponseSpec(audience="personal", timeout=600, live_updates=True, session=_DETAIL_SESSION)
    selected_file: str | None = sl.state(None)

    def __init__(self, workspace: DraftWorkspace) -> None:
        self.workspace = workspace

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        ws = self.workspace
        if (closed := _closed_detail(self, ws)) is not None:
            return closed
        files = ws.attachments
        selected = next((item for item in files if str(item.id) == self.selected_file), None)
        children: list[Any] = [sd.v2.heading(tr(t"draft-editor-files-heading = Files"))]
        if ws.notice:
            children.append(sd.v2.text(Message.join("", ("⚠️ ", ws.notice))))
        children.append(sd.v2.text(_files_text(ws, heading=False)))
        if files:
            children.append(
                sd.v2.select(
                    *(sd.v2.option(item.filename[:100], str(item.id), default=item is selected) for item in files),
                    on_select=self._select_file,
                    key="file",
                    placeholder=tr(t"draft-editor-choose-file = Choose a file to replace or remove…"),
                )
            )
        if selected is not None:
            children.append(
                sd.v2.row(
                    selected.source is not None
                    and selected.status is IntakeStatus.PENDING
                    and sd.v2.button(tr(t"draft-editor-use-file = Use this file"), self._use_file, key="use_file"),
                    sd.v2.button(tr(t"draft-editor-replace-file = Replace"), self._replace_file, key="replace_file"),
                    sd.v2.button(
                        tr(t"draft-editor-remove-file = Remove"),
                        self._remove_file,
                        key="remove_file",
                        style=sl.primitives.ActionStyle.DANGER,
                    ),
                    selected.kind == "schematic"
                    and sd.v2.button(
                        tr(t"draft-editor-main-schematic = Use as main schematic"),
                        self._make_main_schematic,
                        key="main_schematic",
                    ),
                )
            )
        children.extend(
            (
                sd.v2.separator(),
                _done_row(
                    self._done,
                    sd.v2.button(tr(t"draft-editor-add-files = Add files"), self._add_files, key="add_files"),
                ),
            )
        )
        return (sd.v2.panel(*children, accent=DISCORD_BLUE), *history_controls(self.workspace.history))

    def _selected_id(self) -> UUID | None:
        """The selected file, if it is still on the draft."""
        if any(str(item.id) == self.selected_file for item in self.workspace.attachments):
            return UUID(cast(str, self.selected_file))
        return None

    async def _select_file(self, event: sd.SelectionEvent) -> None:
        self.selected_file = event.values[0] if event.values else None

    async def _add_files(self, event: sd.PressEvent) -> None:
        await present_add_files(self.workspace, event)

    async def _remove_file(self, event: sd.PressEvent) -> None:
        if (file_id := self._selected_id()) is not None:
            await self.workspace.remove_file(file_id)
            self.selected_file = None

    async def _use_file(self, event: sd.PressEvent) -> None:
        if (file_id := self._selected_id()) is not None:
            await event.acknowledge()
            await self.workspace.use_file(file_id)

    async def _make_main_schematic(self, event: sd.PressEvent) -> None:
        if (file_id := self._selected_id()) is not None:
            await self.workspace.make_main_schematic(file_id)

    async def _replace_file(self, event: sd.PressEvent) -> None:
        file_id = self._selected_id()
        if file_id is None:
            return

        async def supplied(event: sl.SubmitEvent) -> None:
            source = event.values.get("file")
            if isinstance(source, sl.forms.UploadedFile):
                new_id = await self.workspace.replace_file(file_id, source)
                if new_id is not None:
                    self.selected_file = str(new_id)

        await event.present_form(
            sl.forms.FormSpec(
                tr(t"draft-editor-replace-title = Replace file"),
                (FileField(key="file", label=tr(t"draft-editor-replacement-label = New file"), minimum=1, maximum=1),),
            ),
            key="replace-file",
            on_submit=supplied,
        )

    async def _done(self, event: sd.PressEvent) -> None:
        self.complete(event, None)


class BuildPreviewScreen(sd.Screen[None]):
    """A private message showing the card other people will see once the draft is submitted.

    The card is drawn by the same renderer as posted builds, from preview_build, and redraws
    whenever the draft changes. Images and schematics are not shown: they attach to the build
    only when it is created.
    """

    policy = sd.ResponseSpec(
        audience="personal", timeout=600, live_updates=True, session=sd.SessionSpec("draft-preview")
    )

    def __init__(self, workspace: DraftWorkspace) -> None:
        self.workspace = workspace

    @sl.resource
    async def card(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None:
        """The rendered card, or None while the answers its title needs are missing."""
        ws = self.workspace
        answers = ws.manifest.visible_answers(ws.category, ws.draft.snapshot.answers, origin=ws.draft.origin)
        definitions = {definition.stable_key: definition for definition in await ws.services.tags.public_definitions()}
        build = preview_build(ws.category, answers, definitions)
        if build is None or ws.render_card is None:
            return None
        return await ws.render_card(build)

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        intro = sl.paragraph(
            tr(
                t"draft-editor-preview-intro = **Preview** · This is the card others will see once you submit. "
                t"It updates as you edit; images and schematics appear after submission."
            )
        )
        match self.card.status:
            case sl.resources.Ready(value=card) if card is not None:
                return (intro, card)
            case sl.resources.Ready():
                ws = self.workspace
                needed = field_names(ws.manifest, ws.category, PREVIEW_TITLE_FIELDS.get(ws.category, ()))
                return (intro, sl.paragraph(tr(t"draft-editor-preview-needs = Fill in {needed} to see the preview.")))
            case sl.resources.Failed(previous=None):
                return (intro, sl.paragraph(tr(t"draft-editor-preview-failed = The preview could not be drawn.")))
            case sl.resources.Pending(previous=None):
                return (intro, sl.status(tr(t"draft-editor-preview-loading = Drawing the preview…")))
            case sl.resources.Pending(previous=previous) | sl.resources.Failed(previous=previous):
                assert previous is not None
                # Keep the last card on screen while the next one is drawn, or if drawing it failed.
                return (intro,) if previous.value is None else (intro, previous.value)


def _closed_detail(
    screen: sd.Screen[None], ws: DraftWorkspace
) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...] | None:
    """The final view of an editor message once it is closed, or once the draft can no longer change."""
    if screen.outcome is not None:
        return (sl.status(tr(t"draft-editor-detail-closed = Changes saved. You can dismiss this message.")),)
    if not ws.loaded or ws.phase not in EDITABLE_PHASES:
        return (sl.status(tr(t"draft-editor-detail-locked = This draft can no longer be edited here.")),)
    return None


def _done_row(on_done: PressHandler[sd.Request], *before: Any) -> Any:
    return sd.v2.row(
        *before,
        sd.v2.button(tr(t"draft-editor-done = Done"), on_done, key="done", style=sl.primitives.ActionStyle.PRIMARY),
    )


# Text shared by the panel and its editor messages


def _header(ws: DraftWorkspace, phase: Phase) -> Any:
    """Title, a sentence about the current phase, and any problems to fix."""
    category = ws.manifest.category(ws.category).label
    expires = ws.draft.expires_at.to_stdlib()
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
                t"draft-editor-status-creating = ⏳ Creating your build. "
                t"This usually takes under a minute; this message updates when it is done."
            )
        case Phase.DONE:
            result = ws.result.result if ws.result is not None else None
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
        issue for issue in ws.issues if phase is not Phase.WAITING_FOR_FILES or issue.reason not in _PROCESSING_REASONS
    ]
    if phase in {Phase.NEEDS_CHANGES, Phase.WAITING_FOR_FILES} and issues:
        labels = {field.id: field.label for field in ws.manifest.fields_for(ws.category)}
        lines.extend(Message.join("", ("- ", _issue_text(issue, labels))) for issue in issues)
    if ws.notice:
        lines.append(Message.join("", ("⚠️ ", ws.notice)))
    return sd.v2.text(Message.join("\n", lines))


def _section_text(ws: DraftWorkspace, section: FormSection) -> Message:
    """The section title followed by one line per answer or missing required field."""
    lines = _answer_lines(ws, section.fields, ws.answers, show_empty=False)
    if not lines:
        lines = [tr(t"draft-editor-section-empty = *Optional. Nothing added yet.*")]
    return Message.join("\n", (md(t"**{section.title}**"), *lines))


def _answer_lines(
    ws: DraftWorkspace, fields: Sequence[FormField], answers: Mapping[str, JSONValue], *, show_empty: bool
) -> list[TextLike]:
    """Format "Label: value" lines, flagging missing required fields and invalid values.

    Empty optional fields are skipped unless show_empty is set, as in a section's own editor
    where the submitter needs to see every field they can fill in.
    """
    errors = ws.manifest.validate_answers(ws.category, answers, origin=ws.draft.origin)
    groups = _dimension_groups(fields)
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
        label = md(t"{field.label}: ")
        if _is_empty(value):
            if field.required:
                lines.append(label + tr(t"draft-editor-required = ⚠️ *Required*"))
            elif show_empty:
                lines.append(label + tr(t"draft-editor-not-set = *Not set*"))
            continue
        line = label + _value_text(ws, field, value)
        if (reason := errors.get(field.id)) is not None:
            line = Message.join("", (line, " — ⚠️ ", _problem_text(reason)))
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
    label = Message.join("", (group.label, ": "))
    reason = next((errors[field_id] for field_id in group.field_ids if field_id in errors), None)
    if all(value is None for value in values):
        if reason is not None:
            return label + tr(t"draft-editor-required = ⚠️ *Required*")
        return label + tr(t"draft-editor-not-set = *Not set*") if show_empty else None
    size = format_dimensions(tuple(value if isinstance(value, int) else None for value in values))
    line = label + md(t"{size}")
    return line if reason is None else Message.join("", (line, " — ⚠️ ", _problem_text(reason)))


def _value_text(ws: DraftWorkspace, field: FormField, value: JSONValue) -> TextLike:
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


def _files_text(ws: DraftWorkspace, *, heading: bool = True) -> Message:
    """List each file with its kind, adding a status only when the file needs attention."""
    lines: list[TextLike] = [tr(t"draft-editor-files-title = **Files**")] if heading else []
    editable = ws.phase in EDITABLE_PHASES
    if not ws.attachments:
        lines.append(
            tr(t"draft-editor-files-empty = *No files yet. Add your schematic and any images or videos.*")
            if editable
            else tr(t"draft-editor-files-none = *No files.*")
        )
    for item in ws.attachments:
        kind = {
            "schematic": tr(t"draft-editor-file-kind-schematic = Schematic"),
            "image": tr(t"draft-editor-file-kind-image = Image"),
            "video": tr(t"draft-editor-file-kind-video = Video"),
        }.get(item.kind, tr(t"draft-editor-file-kind-unknown = Unsupported file type"))
        status: TextLike | None
        match item.status:
            case IntakeStatus.READY:
                status = None
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
        parts: list[TextLike] = [md(t"**{item.filename}**"), kind]
        if status is not None:
            parts.append(status)
        lines.append(Message.join(" · ", parts))
    return Message.join("\n", lines)


def field_names(manifest: FormManifest, category: str, field_ids: Iterable[str]) -> Message:
    """Join field labels for a sentence, naming a size once (as "Door size") instead of its parts."""
    labels = {field.id: field.label for field in manifest.fields_for(category)}
    names: list[TextLike] = []
    for field_id in field_ids:
        group = next((group for group in DIMENSION_GROUPS if field_id in group.field_ids), None)
        name = group.label if group is not None else md(t"{(labels.get(field_id, field_id))}")
        if name not in names:
            names.append(name)
    return Message.join(", ", names)


# Modals


async def present_chunk_form(ws: DraftWorkspace, chunk: FormSection, event: sd.PressEvent) -> None:
    """Show a modal for up to five typed fields and save them together."""
    # Save against the revision the modal showed, so a stale modal cannot overwrite a newer edit.
    base_revision = ws.draft.snapshot.revision
    answers = ws.answers
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


async def present_add_files(ws: DraftWorkspace, event: sd.PressEvent) -> None:
    """Show a modal accepting up to MAX_FILES_PER_UPLOAD files and attach them to the draft."""

    async def supplied(event: sl.SubmitEvent) -> None:
        uploaded = event.values.get("files")
        files = uploaded if isinstance(uploaded, tuple) else (uploaded,) if uploaded is not None else ()
        await ws.add_files(tuple(item for item in files if isinstance(item, sl.forms.UploadedFile)))

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
    services: BotServices,
    actor_id: int,
    draft_id: UUID,
    *,
    changes: TopicHandle,
    render_card: CardRenderer | None = None,
    notice: sl.TextLike = "",
) -> DraftEditorScreen:
    """Open a saved draft with the form revision it was created under.

    Catalogue options are loaded up front so the summary can show their labels.

    Args:
        render_card: Draws the build card for Preview; without it the panel has no Preview button.
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
    workspace = DraftWorkspace(services, actor_id, draft, manifest, changes=changes, render_card=render_card)
    await workspace.reload()
    await workspace.load_options(workspace.fields)
    workspace.notice = notice
    return DraftEditorScreen(workspace)
