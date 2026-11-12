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
from enum import Enum, auto
from typing import Any, cast, override
from uuid import UUID, uuid4

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.edit_controller import EditController
from squid.bot.submission import draft_intake
from squid.bot.submission.ui import controls as build_controls
from squid.bot.submission.ui.editor import BuildEditorScreen, EditorSection
from squid.bot.submission.ui.sections import (
    DETAIL_SESSION,
    DIMENSION_GROUPS,
    closed_detail,
    dimension_groups,
    done_row,
    is_empty,
    problem_text,
    section_text,
)
from squid.bot.ui import DISCORD_BLUE, DISCORD_GREEN, DISCORD_RED, DISCORD_YELLOW
from squid.builds.models import Build
from squid.core.actors import Actor
from squid.core.errors import JSONValue, SquidError
from squid.core.i18n import tr
from squid.runtime import BotServices
from squid.submissions.application import FinalizationJobSnapshot, StoredDraft
from squid.submissions.application.attachment_edits import DRAFT_ATTACHMENTS, AttachmentValues, FileMembership
from squid.submissions.application.build_target import preview_build
from squid.submissions.application.edits import DRAFT_ANSWERS, answer_values, draft_target
from squid.submissions.application.intake import SuppliedAttachment, classify_supplied_file
from squid.submissions.domain import (
    ChoiceOption,
    DraftStatus,
    FormField,
    FormManifest,
    FormSection,
    SubmissionOrigin,
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

MAX_FILES_PER_UPLOAD = 4
"""Files accepted by one Add files modal, matching the four file options of /build submit."""


type CardRenderer = Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]]
"""Draws a build card exactly as posted builds are drawn; it needs the bot, which screens do not hold."""


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
        resolve_actor: Callable[[], Awaitable[Actor]] | None = None,
    ) -> None:
        self.services = services

        async def actor() -> Actor:
            return Actor(account_id=actor_id) if resolve_actor is None else await resolve_actor()

        self.resolve_actor = actor
        self.edits = EditController(services.edits, actor, self.reload)
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
    def origin(self) -> SubmissionOrigin:
        return self.draft.origin

    @property
    def stored_answers(self) -> Mapping[str, JSONValue]:
        return self.draft.snapshot.answers

    @property
    def revision(self) -> int:
        return self.draft.snapshot.revision

    @property
    def editable(self) -> bool:
        return self.loaded and self.phase in EDITABLE_PHASES

    def history_controls(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return self.edits.controls()

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
        missing = [field.id for field in self.fields if field.required and is_empty(answers.get(field.id))]
        for group in dimension_groups(self.fields):
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
        if not values:
            return
        try:
            await self.edits.bind(
                DRAFT_ANSWERS,
                draft_target(self._draft_id),
                self.draft.snapshot.revision if base_revision is None else base_revision,
            ).save(answer_values(values), tr(t"draft-history-answers = Edit submission answers"))
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
            self.edits.forget({draft_target(self._draft_id).identity})
        await self.reload()

    async def add_files(self, files: Sequence[sl.forms.UploadedFile], *, base_revision: int | None = None) -> None:
        revision = self.draft.snapshot.revision if base_revision is None else base_revision
        memberships = tuple(
            FileMembership(
                source_id=uuid4(),
                present=True,
                filename=file.name,
                kind=classify_supplied_file(file.name, file.media_type),
            )
            for file in files
        )
        failed = 0
        await self.services.submission_uploads.stage(self._draft_id, await self.resolve_actor(), revision, memberships)
        for item, file in zip(memberships, files, strict=True):
            try:
                await draft_intake.register_uploaded_file(
                    self.services, self._draft_id, self.actor_id, item.source_id, file
                )
            except Exception:
                await self.services.submission_intake.fail(self._draft_id, self.actor_id, item.source_id)
                failed += 1
        await self.edits.bind(DRAFT_ATTACHMENTS, draft_target(self._draft_id), revision).save(
            AttachmentValues(files=memberships).edit_values(), tr(t"draft-history-add-files = Add submission files")
        )
        self.notice = (
            tr(
                t"draft-editor-files-failed = {failed} of the files could not be added. "
                t"They are listed under Files so you can replace or remove them."
            )
            if failed
            else ""
        )

    async def remove_file(self, file_id: UUID) -> None:
        await self.edits.bind(DRAFT_ATTACHMENTS, draft_target(self._draft_id), self.draft.snapshot.revision).save(
            AttachmentValues(files=(FileMembership(source_id=file_id, present=False),)).edit_values(),
            tr(t"draft-history-remove-file = Remove submission file"),
        )

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
        await self.edits.bind(DRAFT_ATTACHMENTS, draft_target(self._draft_id), self.draft.snapshot.revision).save(
            AttachmentValues(primary=file_id, select_primary=True).edit_values(),
            tr(t"draft-history-primary = Choose primary schematic"),
        )

    async def replace_file(
        self, file_id: UUID, source: sl.forms.UploadedFile, *, base_revision: int | None = None
    ) -> UUID | None:
        """Register hidden replacement bytes, then atomically swap the two references."""
        revision = self.draft.snapshot.revision if base_revision is None else base_revision
        replacement = FileMembership(
            source_id=uuid4(),
            present=True,
            filename=source.name,
            kind=classify_supplied_file(source.name, source.media_type),
        )
        await self.services.submission_uploads.stage(
            self._draft_id, await self.resolve_actor(), revision, (replacement,)
        )
        try:
            await draft_intake.register_uploaded_file(
                self.services, self._draft_id, self.actor_id, replacement.source_id, source
            )
        except Exception:
            self.notice = tr(
                t"draft-editor-replacement-failed = The replacement could not be added. Try again or remove it."
            )
            return None
        await self.edits.bind(DRAFT_ATTACHMENTS, draft_target(self._draft_id), revision).save(
            AttachmentValues(files=(FileMembership(source_id=file_id, present=False), replacement)).edit_values(),
            tr(t"draft-history-replace-file = Replace submission file"),
        )
        self.notice = ""
        return replacement.source_id


class DraftEditorScreen(BuildEditorScreen[None, DraftWorkspace]):
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
        return (sd.v2.panel(*children, accent=accent), *ws.edits.controls())

    def _overview(self, phase: Phase) -> list[Any]:
        """One row per section and the Files row, then the Submit row."""
        ws = self.workspace
        children = self.section_overview(
            [EditorSection(section.id, section_text(ws, section)) for section in ws.sections]
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
        children: list[Any] = [sd.v2.text(section_text(ws, section)) for section in ws.sections]
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


class FilesEditorScreen(sd.Screen[None]):
    """A private message for adding, replacing, and removing the draft's files."""

    policy = sd.ResponseSpec(audience="personal", timeout=600, live_updates=True, session=DETAIL_SESSION)
    selected_file: str | None = sl.state(None)

    def __init__(self, workspace: DraftWorkspace) -> None:
        self.workspace = workspace

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        ws = self.workspace
        if (closed := closed_detail(self, ws)) is not None:
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
                done_row(
                    self._done,
                    sd.v2.button(tr(t"draft-editor-add-files = Add files"), self._add_files, key="add_files"),
                ),
            )
        )
        return (sd.v2.panel(*children, accent=DISCORD_BLUE), *self.workspace.edits.controls())

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

        revision = self.workspace.draft.snapshot.revision

        async def supplied(event: sl.SubmitEvent) -> None:
            source = event.values.get("file")
            if isinstance(source, sl.forms.UploadedFile):
                new_id = await self.workspace.replace_file(file_id, source, base_revision=revision)
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
                return (intro, sl.paragraph(tr(t"draft-editor-preview-failed = The preview could not be drawn.")))
            case sl.resources.Failed(previous=None):
                return (intro, sl.paragraph(tr(t"draft-editor-preview-failed = The preview could not be drawn.")))
            case sl.resources.Pending(previous=None):
                return (intro, sl.status(tr(t"draft-editor-preview-loading = Drawing the preview…")))
            case sl.resources.Pending(previous=previous) | sl.resources.Failed(previous=previous):
                assert previous is not None
                # Keep the last card on screen while the next one is drawn, or if drawing it failed.
                return (intro,) if previous.value is None else (intro, previous.value)


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


async def present_add_files(ws: DraftWorkspace, event: sd.PressEvent) -> None:
    """Show a modal accepting up to MAX_FILES_PER_UPLOAD files and attach them to the draft."""

    revision = ws.draft.snapshot.revision

    async def supplied(event: sl.SubmitEvent) -> None:
        uploaded = event.values.get("files")
        files = uploaded if isinstance(uploaded, tuple) else (uploaded,) if uploaded is not None else ()
        await ws.add_files(
            tuple(item for item in files if isinstance(item, sl.forms.UploadedFile)), base_revision=revision
        )

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
        return md(t"{label}: ") + problem_text(issue.reason)
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


async def draft_editor(
    services: BotServices,
    actor_id: int,
    draft_id: UUID,
    *,
    changes: TopicHandle,
    render_card: CardRenderer | None = None,
    resolve_actor: Callable[[], Awaitable[Actor]] | None = None,
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
    workspace = DraftWorkspace(
        services, actor_id, draft, manifest, changes=changes, render_card=render_card, resolve_actor=resolve_actor
    )
    await workspace.reload()
    await workspace.load_options(workspace.fields)
    workspace.notice = notice
    return DraftEditorScreen(workspace)
