"""The private draft panel behind /build submit and /build drafts.

The panel uses the draft's pinned form schema, saves answers against the revision the submitter
was looking at, and shows each stage of the submission lifecycle.
"""

from dataclasses import replace
from typing import override
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from whenever import Instant

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ui.drafts import (
    FILES_FOCUS,
    DraftEditorScreen,
    Phase,
    _DimensionsField,
    _modal_chunks,
    _parse_ticks,
    draft_editor,
)
from squid.runtime import BotServices
from squid.submissions.application import (
    AppliedDraftChange,
    FinalizationJobSnapshot,
    FormOptionSet,
    StoredDraft,
    SubmissionDraftService,
    SubmissionFinalizationService,
    SubmissionFormService,
    build_submission_manifest,
)
from squid.submissions.application.drafts import DraftActor
from squid.submissions.application.finalization import SubmissionRequestResult
from squid.submissions.application.forms import CURRENT_SUBMISSION_SCHEMA, CURRENT_SUBMISSION_SCHEMA_REVISION
from squid.submissions.application.intake import SubmissionAttachmentIntake, SuppliedAttachment
from squid.submissions.domain import (
    ChoiceOption,
    DraftChange,
    DraftRevisionConflictError,
    DraftSnapshot,
    DraftStatus,
    FormManifest,
    SubmissionOrigin,
)
from squid.submissions.domain.finalization import (
    FinalizationJobStatus,
    FinalizedBuild,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)
from squid.submissions.domain.intake import IntakeStatus
from squid_reactivity import LocalTopicBus
from squid_ui.forms import FormValueError
from squid_ui.primitives import RoutedButton
from squid_ui.testing import find_all, labels, texts
from squid_ui.text import source_text
from squid_ui_discord.modal import build_form_modal
from squid_ui_discord.testing import commit_render, payload_problems
from tests.support.services import bot_services


class Drafts(SubmissionDraftService):
    """Apply changes through DraftSnapshot revision checks while keeping the snapshot in memory."""

    def __init__(self, draft: StoredDraft) -> None:
        self.current = draft
        self.actors: list[DraftActor] = []
        self.changes: list[DraftChange] = []

    @override
    async def get_accessible(self, draft_id: UUID, actor: DraftActor) -> StoredDraft:
        assert draft_id == self.current.snapshot.id
        self.actors.append(actor)
        return self.current

    @override
    async def apply_change(
        self,
        draft_id: UUID,
        account_id: DraftActor,
        change: DraftChange,
        *,
        locale: str | None,
        now: Instant | None = None,
    ) -> AppliedDraftChange:
        self.actors.append(account_id)
        self.changes.append(change)
        self.current = replace(self.current, snapshot=self.current.snapshot.apply(change))
        return AppliedDraftChange(self.current)


class Forms(SubmissionFormService):
    def __init__(self) -> None:
        pass

    @override
    async def manifest_revision(self, schema_id: str, revision: int, *, locale: str | None) -> FormManifest | None:
        return build_submission_manifest(revision=revision)

    @override
    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        # Thirty versions force a second picker page; tag catalogues stay small.
        if source == "approved_source_versions":
            choices = tuple(ChoiceOption(f"Java 1.{minor}", f"Java 1.{minor}") for minor in range(30, 0, -1))
        else:
            choices = (ChoiceOption("full_lamp", "Full Lamp"), ChoiceOption("funnel", "Funnel"))
        return FormOptionSet(source, category, 1, choices)


class Intake(SubmissionAttachmentIntake):
    def __init__(self) -> None:
        self.files: tuple[SuppliedAttachment, ...] = ()

    @override
    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[SuppliedAttachment, ...]:
        return self.files


class Finalization(SubmissionFinalizationService):
    def __init__(self) -> None:
        self.result: SubmissionRequestResult | None = None

    @override
    async def status(self, draft_id: UUID, account_id: DraftActor) -> SubmissionRequestResult | None:
        return self.result


def setup(category: str = "other") -> tuple[BotServices, Drafts]:
    now = Instant.now()
    draft = StoredDraft(
        snapshot=DraftSnapshot(uuid4(), 7, CURRENT_SUBMISSION_SCHEMA, CURRENT_SUBMISSION_SCHEMA_REVISION, category),
        origin=SubmissionOrigin.DISCORD,
        created_at=now,
        updated_at=now,
        expires_at=now.add(days=7, days_assumed_24h_ok=True),
    )
    drafts = Drafts(draft)
    services = bot_services(
        submission_drafts=drafts,
        submission_forms=Forms(),
        submission_finalization=Finalization(),
        submission_intake=Intake(),
    )
    return services, drafts


async def open_editor(services: BotServices, drafts: Drafts, actor: int = 7) -> DraftEditorScreen:
    editor = await draft_editor(
        services,
        actor,
        drafts.current.snapshot.id,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
    )
    await editor.projection.reload()
    return editor


def rendered_text(editor: DraftEditorScreen) -> str:
    return "\n".join(texts(editor.render()))


async def test_reopening_reads_saved_answers_and_pinned_manifest() -> None:
    services, drafts = setup()
    first = await open_editor(services, drafts)
    await first._save_values({"display_name": "Saved name"})
    reopened = await open_editor(services, drafts)
    assert reopened is not first
    assert reopened.draft.snapshot.answers["display_name"] == "Saved name"
    assert reopened.manifest.revision == reopened.draft.snapshot.schema_revision
    assert "Build name: Saved name" in rendered_text(reopened)
    assert "Submit" in labels(reopened.render())


async def test_staff_writes_use_actor_and_preserve_owner() -> None:
    services, drafts = setup()
    editor = await open_editor(services, drafts, actor=99)
    await editor._save_values({"description": "Staff correction"})
    assert editor.draft.snapshot.owner_account_id == 7
    assert all(actor == 99 for actor in drafts.actors)
    assert drafts.changes[0].client_instance_id == "discord-editor"


async def test_stale_modal_does_not_overwrite_a_newer_edit() -> None:
    services, drafts = setup()
    editor = await open_editor(services, drafts)
    base_revision = editor.draft.snapshot.revision
    await editor._save_values({"display_name": "Newer value"})
    await editor._save_values({"display_name": "Stale modal"}, base_revision=base_revision)
    assert editor.draft.snapshot.answers["display_name"] == "Newer value"
    assert source_text(editor.notice) == DraftRevisionConflictError(expected=0, actual=1).public_detail()


def test_every_visible_field_comes_from_the_manifest() -> None:
    services, drafts = setup()
    manifest = build_submission_manifest()
    editor = DraftEditorScreen(
        services,
        7,
        drafts.current,
        manifest,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
    )
    assert {field.id for field in editor.fields} <= {field.id for field in manifest.fields_for("other")}
    assert "source_version" in {field.id for field in editor.fields}
    assert "rights_attestation" not in {field.id for field in editor.fields}


@pytest.mark.parametrize("category", ["door", "extender", "utility", "entrance", "other"])
async def test_overview_is_one_card_within_discord_limits(category: str) -> None:
    services, drafts = setup(category)
    editor = await open_editor(services, drafts)
    root = sd.MessageRoot(editor, access=sd.Owner(7), timeout=None)
    payload = commit_render(root).to_components()
    assert len(payload) == 1
    assert payload[0]["type"] == 17
    assert not payload_problems(payload)
    # Every section has its own Edit button, and the files row offers to add files.
    assert labels(editor.render()).count("Edit") == len(editor.sections)
    assert "Add files" in labels(editor.render())
    for section in editor.sections:
        for chunk in _modal_chunks(section):
            event = Mock(spec=sl.PressEvent, present_form=AsyncMock())
            await editor._edit_form(chunk.id, event)
            modal = build_form_modal(event.present_form.call_args.args[0], on_submit=AsyncMock())
            assert 1 <= len(modal.children) <= 5


async def test_required_count_treats_a_size_as_one_field() -> None:
    services, drafts = setup("door")
    editor = await open_editor(services, drafts)
    text = rendered_text(editor)
    # Creators, build size, Minecraft version, schematic access, door size, and door type.
    assert "6 required fields are empty" in text
    assert "Build size: ⚠️ *Required*" in text
    submit = next(button for button in find_all(editor.render(), sl.primitives.Button) if button.key == "submit")
    assert submit.disabled


async def test_size_modal_saves_three_fields_together_and_rejects_stale_edits() -> None:
    services, drafts = setup()
    editor = await open_editor(services, drafts)
    # The section also holds the Minecraft version picker, so Edit opens its focused view.
    await editor._edit_section("dimensions_versions", Mock(spec=sl.PressEvent))
    assert editor.focus == "dimensions_versions"
    event = Mock(spec=sl.PressEvent, present_form=AsyncMock())
    await editor._edit_form("dimensions_versions_0", event)
    spec = event.present_form.call_args.args[0]
    entries = {entry.key: entry for entry in spec.items}
    assert set(entries) == {"capture_size", "version_compatibility"}
    assert entries["capture_size"].required
    submit = event.present_form.call_args.kwargs["on_submit"]
    await submit(Mock(spec=sl.SubmitEvent, values={"capture_size": "3x4x5", "version_compatibility": None}))
    assert len(drafts.changes) == 1
    answers = editor.draft.snapshot.answers
    assert (answers["capture_width"], answers["capture_height"], answers["capture_depth"]) == (3, 4, 5)
    assert "Build size: 3 x 4 x 5" in rendered_text(editor)

    await submit(Mock(spec=sl.SubmitEvent, values={"capture_size": "9x9x9", "version_compatibility": None}))
    assert editor.draft.snapshot.answers["capture_width"] == 3
    assert source_text(editor.notice) == DraftRevisionConflictError(expected=0, actual=1).public_detail()


async def test_size_without_depth_leaves_depth_missing() -> None:
    services, drafts = setup()
    editor = await open_editor(services, drafts)
    event = Mock(spec=sl.PressEvent, present_form=AsyncMock())
    await editor._edit_form("dimensions_versions_0", event)
    submit = event.present_form.call_args.kwargs["on_submit"]
    await submit(Mock(spec=sl.SubmitEvent, values={"capture_size": "5 x 7", "version_compatibility": None}))
    assert "capture_depth" not in editor.draft.snapshot.answers
    assert "Build size: 5 x 7 x ? — ⚠️ Required" in rendered_text(editor)


def test_size_input_rejects_text_that_is_not_a_size() -> None:
    field = _DimensionsField(key="capture_size", label="Build size", required=True)
    assert field.parse(" 5x7x4 ") == "5x7x4"
    assert field.parse("5*7") == "5*7"
    with pytest.raises(FormValueError):
        field.parse("five by seven")


@pytest.mark.parametrize(("text", "ticks"), [("30gt", 30), ("15rt", 30), ("1.5s", 30), ("3 redstone ticks", 6)])
def test_timings_accept_explicit_units(text: str, ticks: int) -> None:
    assert _parse_ticks(text) == ticks


@pytest.mark.parametrize("text", ["30", "1.5", "soon", "-2s"])
def test_timings_reject_missing_units_and_nonsense(text: str) -> None:
    with pytest.raises(ValueError, match=r"unit|such as"):
        _parse_ticks(text)


async def test_saved_ticks_show_as_seconds_and_prefill_the_modal_losslessly() -> None:
    services, drafts = setup("door")
    editor = await open_editor(services, drafts)
    await editor._save_values({"opening_time": 7})
    assert "Opening time: 0\\.35s" in rendered_text(editor)
    event = Mock(spec=sl.PressEvent, present_form=AsyncMock())
    await editor._edit_section("door_timing", event)
    entry = next(item for item in event.present_form.call_args.args[0].items if item.key == "opening_time")
    assert entry.format(entry.default) == "0.35s"
    assert _parse_ticks("0.35s") == 7


async def test_public_visibility_reveals_required_license_and_permission() -> None:
    services, drafts = setup()
    editor = await open_editor(services, drafts)
    rights = next(section for section in editor.sections if section.id == "rights_privacy")
    assert [field.id for field in rights.fields] == ["schematic_visibility"]
    await editor._save_values({"schematic_visibility": "public_download"})
    rights = next(section for section in editor.sections if section.id == "rights_privacy")
    assert [field.id for field in rights.fields] == ["schematic_visibility", "schematic_license", "rights_attestation"]
    text = rendered_text(editor)
    assert "Who can download the schematic: Anyone" in text
    assert "Schematic license: ⚠️ *Required*" in text


async def test_picker_opens_on_the_page_of_the_current_choice() -> None:
    services, drafts = setup()
    editor = await open_editor(services, drafts)
    # The stub catalogue lists Java 1.30 down to 1.1, so 1.2 is on the second page.
    await editor._save_values({"source_version": "Java 1.2"})
    await editor._edit_section("dimensions_versions", Mock(spec=sl.PressEvent))
    text = rendered_text(editor)
    assert "Page 2 of 2." in text
    assert "Choices on other pages are kept" not in text
    select = find_all(editor.render(), sl.primitives.SelectMenu)[0]
    assert [option.label for option in select.options if option.default] == ["Java 1.2"]
    await editor._select_value("source_version", Mock(spec=sl.SelectionEvent, values=("Java 1.1",)))
    assert editor.draft.snapshot.answers["source_version"] == "Java 1.1"
    await editor._close_focus(Mock(spec=sl.PressEvent))
    assert "Minecraft version: Java 1\\.1" in rendered_text(editor)


async def test_files_view_lists_files_with_their_state() -> None:
    services, drafts = setup()
    intake = services.submission_intake
    assert isinstance(intake, Intake)
    intake.files = (
        SuppliedAttachment(uuid4(), "door.litematic", "schematic", IntakeStatus.READY),
        SuppliedAttachment(uuid4(), "clip.mp4", "video", IntakeStatus.FAILED),
        SuppliedAttachment(uuid4(), "old.png", "image", IntakeStatus.DISCARDED),
    )
    editor = await open_editor(services, drafts)
    text = rendered_text(editor)
    assert "**door\\.litematic** · Schematic · Received" in text
    assert "⚠️ Upload failed" in text
    assert "old" not in text
    await editor._open_files(Mock(spec=sl.PressEvent))
    assert editor.focus == FILES_FOCUS
    await editor._select_attachment(Mock(spec=sl.SelectionEvent, values=(str(intake.files[0].id),)))
    assert {"Replace", "Remove", "Use as main schematic", "Add files", "Done"} <= set(labels(editor.render()))


async def test_live_projection_moves_from_creating_to_done() -> None:
    services, drafts = setup()
    finalization = services.submission_finalization
    assert isinstance(finalization, Finalization)
    editor = await open_editor(services, drafts)
    drafts.current = replace(drafts.current, snapshot=replace(drafts.current.snapshot, status=DraftStatus.PROCESSING))
    await editor.projection.reload()
    assert editor.phase is Phase.CREATING
    assert "Creating your build" in rendered_text(editor)
    assert "Submit" not in labels(editor.render())
    assert "Edit" not in labels(editor.render())

    drafts.current = replace(drafts.current, snapshot=replace(drafts.current.snapshot, status=DraftStatus.SUBMITTED))
    finalization.result = FinalizationJobSnapshot(
        uuid4(),
        drafts.current.snapshot.id,
        1,
        FinalizationJobStatus.COMPLETED,
        1,
        Instant.now(),
        result=FinalizedBuild(42),
    )
    await editor._reload()
    assert editor.phase is Phase.DONE
    assert "saved as **#42**" in rendered_text(editor)
    (button,) = find_all(editor.render(), RoutedButton)
    assert button.route_id == "r:builds:42:edit"


async def test_needs_changes_lists_issues_the_submitter_can_act_on() -> None:
    services, drafts = setup()
    drafts.current = replace(
        drafts.current,
        snapshot=replace(drafts.current.snapshot, status=DraftStatus.NEEDS_ATTENTION),
        preparation_issues=(
            SubmissionAttentionIssue("creators", SubmissionAttentionReason.TOO_SHORT),
            SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_PRIMARY_REQUIRED),
        ),
    )
    editor = await open_editor(services, drafts)
    assert editor.phase is Phase.NEEDS_CHANGES
    text = rendered_text(editor)
    assert "- Creators: Too short" in text
    assert "Choose the main one under **Files**" in text
    assert "Submit" in labels(editor.render())


async def test_waiting_for_files_hides_processing_issues_and_submit() -> None:
    services, drafts = setup()
    drafts.current = replace(
        drafts.current,
        snapshot=replace(drafts.current.snapshot, status=DraftStatus.NEEDS_ATTENTION),
        preparation_issues=(SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_PROCESSING),),
        preparation_retry_at=Instant.now(),
    )
    editor = await open_editor(services, drafts)
    assert editor.phase is Phase.WAITING_FOR_FILES
    text = rendered_text(editor)
    assert "Waiting for your files" in text
    assert "still being processed" not in text
    assert "Refresh" in labels(editor.render())
    assert "Submit" not in labels(editor.render())
