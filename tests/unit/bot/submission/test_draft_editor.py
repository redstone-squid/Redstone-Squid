"""The private draft panel behind /build submit and /build drafts.

The panel uses the draft's pinned form schema, saves answers against the revision the submitter
was looking at, and shows each stage of the submission lifecycle. Section editors, the files
editor, and the preview are separate messages that share the panel's DraftWorkspace.
"""

from dataclasses import replace
from typing import Any, override
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from whenever import Instant

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ui.drafts import (
    BuildPreviewScreen,
    DraftEditorScreen,
    FilesEditorScreen,
    Phase,
    SectionEditorScreen,
    _DimensionsField,
    _modal_chunks,
    _parse_ticks,
    draft_editor,
    present_chunk_form,
)
from squid.builds.models import Build
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
from squid.tags.application import TagService
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagDefinition
from squid_reactivity import LocalTopicBus
from squid_ui.forms import FormValueError
from squid_ui.primitives import Button, RoutedButton, SelectMenu
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


class Tags(TagService):
    def __init__(self) -> None:
        pass

    @override
    async def public_definitions(self) -> list[TagDefinition]:
        return [
            TagDefinition(
                id=1,
                stable_key="full_lamp",
                display_name="Full Lamp",
                authority=TagAuthority.OFFICIAL,
                semantic_kind=TagSemanticKind.PATTERN,
                value_type=TagValueType.NONE,
                moderation_status=TagModerationStatus.APPROVED,
            )
        ]


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
        tags=Tags(),
    )
    return services, drafts


async def open_editor(
    services: BotServices, drafts: Drafts, actor: int = 7, *, render_card: Any = None
) -> DraftEditorScreen:
    editor = await draft_editor(
        services,
        actor,
        drafts.current.snapshot.id,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
        render_card=render_card,
    )
    await editor.workspace.projection.reload()
    return editor


def rendered_text(screen: sd.Screen[Any]) -> str:
    return "\n".join(texts(screen.render()))


def press() -> Mock:
    return Mock(spec=sd.PressEvent, present_form=AsyncMock())


async def submit_modal(event: Mock, values: dict[str, Any]) -> None:
    await event.present_form.call_args.kwargs["on_submit"](Mock(spec=sl.SubmitEvent, values=values))


async def test_reopening_reads_saved_answers_and_pinned_manifest() -> None:
    services, drafts = setup()
    first = await open_editor(services, drafts)
    await first.workspace.save_values({"display_name": "Saved name"})
    reopened = await open_editor(services, drafts)
    assert reopened.workspace is not first.workspace
    assert reopened.workspace.draft.snapshot.answers["display_name"] == "Saved name"
    assert reopened.workspace.manifest.revision == reopened.workspace.draft.snapshot.schema_revision
    assert "Build name: Saved name" in rendered_text(reopened)
    assert "Submit" in labels(reopened.render())


async def test_staff_writes_use_actor_and_preserve_owner() -> None:
    services, drafts = setup()
    editor = await open_editor(services, drafts, actor=99)
    await editor.workspace.save_values({"description": "Staff correction"})
    assert editor.workspace.draft.snapshot.owner_account_id == 7
    assert all(actor == 99 for actor in drafts.actors)
    assert drafts.changes[0].client_instance_id == "discord-editor"


async def test_stale_modal_does_not_overwrite_a_newer_edit() -> None:
    services, drafts = setup()
    ws = (await open_editor(services, drafts)).workspace
    base_revision = ws.draft.snapshot.revision
    await ws.save_values({"display_name": "Newer value"})
    await ws.save_values({"display_name": "Stale modal"}, base_revision=base_revision)
    assert ws.draft.snapshot.answers["display_name"] == "Newer value"
    assert source_text(ws.notice) == DraftRevisionConflictError(expected=0, actual=1).public_detail()


async def test_discord_drafts_do_not_ask_for_the_exported_version() -> None:
    services, drafts = setup()
    ws = (await open_editor(services, drafts)).workspace
    manifest_fields = {field.id for field in ws.manifest.fields_for("other")}
    assert {field.id for field in ws.fields} <= manifest_fields
    assert "source_version" not in {field.id for field in ws.fields}
    assert "rights_attestation" not in {field.id for field in ws.fields}


@pytest.mark.parametrize("category", ["door", "extender", "utility", "entrance", "other"])
async def test_overview_is_one_card_within_discord_limits(category: str) -> None:
    services, drafts = setup(category)
    editor = await open_editor(services, drafts, render_card=AsyncMock())
    root = sd.MessageRoot(editor, access=sd.Owner(7), timeout=None)
    payload = commit_render(root).to_components()
    assert len(payload) == 1
    assert payload[0]["type"] == 17
    assert not payload_problems(payload)
    # Every section has its own Edit button, and the files row offers to add files.
    assert labels(editor.render()).count("Edit") == len(editor.workspace.sections)
    assert {"Add files", "Preview", "Submit"} <= set(labels(editor.render()))
    for section in editor.workspace.sections:
        for chunk in _modal_chunks(section):
            event = press()
            await present_chunk_form(editor.workspace, chunk, event)
            modal = build_form_modal(event.present_form.call_args.args[0], on_submit=AsyncMock())
            assert 1 <= len(modal.children) <= 5


async def test_required_fields_are_counted_until_the_draft_is_ready() -> None:
    services, drafts = setup("door")
    editor = await open_editor(services, drafts)
    text = rendered_text(editor)
    # Creators, schematic access, door size, and door type; the build size is optional.
    assert "4 required fields are empty" in text
    assert "Door size: ⚠️ *Required*" in text
    assert "Build size" not in text
    (submit,) = (button for button in find_all(editor.render(), Button) if button.key == "submit")
    assert submit.disabled

    await editor.workspace.save_values(
        {
            "creators": ["Alice"],
            "schematic_visibility": "reviewer_only",
            "opening_width": 2,
            "opening_height": 2,
            "door_orientation": "door",
        }
    )
    assert "Ready to submit." in rendered_text(editor)
    (submit,) = (button for button in find_all(editor.render(), Button) if button.key == "submit")
    assert not submit.disabled


async def test_modal_sections_open_in_place_and_keep_the_overview() -> None:
    services, drafts = setup()
    editor = await open_editor(services, drafts)
    editor.open_child = AsyncMock()  # type: ignore[method-assign]
    event = press()
    await editor._edit_section("dimensions_versions", event)
    editor.open_child.assert_not_awaited()
    entries = {entry.key: entry for entry in event.present_form.call_args.args[0].items}
    assert set(entries) == {"capture_size", "version_compatibility"}
    assert not entries["capture_size"].required

    await submit_modal(event, {"capture_size": "3x4x5", "version_compatibility": None})
    answers = editor.workspace.draft.snapshot.answers
    assert (answers["capture_width"], answers["capture_height"], answers["capture_depth"]) == (3, 4, 5)
    assert "Build size: 3 x 4 x 5" in rendered_text(editor)

    # The modal was opened at revision 0, so a second submission from it is stale.
    await submit_modal(event, {"capture_size": "9x9x9", "version_compatibility": None})
    assert editor.workspace.draft.snapshot.answers["capture_width"] == 3
    assert source_text(editor.workspace.notice) == DraftRevisionConflictError(expected=0, actual=1).public_detail()


async def test_catalogue_sections_open_a_separate_editor_message() -> None:
    services, drafts = setup("door")
    editor = await open_editor(services, drafts)
    editor.open_child = AsyncMock()  # type: ignore[method-assign]
    event = press()
    await editor._edit_section("patterns", event)
    event.present_form.assert_not_awaited()
    child = editor.open_child.call_args.args[1]
    assert isinstance(child, SectionEditorScreen)
    assert child.workspace is editor.workspace
    # The overview is unchanged: it still lists every section with its Edit button.
    assert labels(editor.render()).count("Edit") == len(editor.workspace.sections)


async def test_a_choice_in_an_editor_message_redraws_the_panel() -> None:
    services, drafts = setup("door")
    editor = await open_editor(services, drafts)
    panel = sd.MessageRoot(editor, access=sd.Owner(7), timeout=None)
    commit_render(panel)
    child = SectionEditorScreen(editor.workspace, "patterns")
    commit_render(sd.MessageRoot(child, access=sd.Owner(7), timeout=None))

    assert not panel.runtime.dirty

    await child._select_value("patterns", Mock(spec=sd.SelectionEvent, values=("full_lamp",)))

    # The save went through the shared workspace, so the panel's message is scheduled to redraw.
    assert panel.runtime.dirty
    assert editor.workspace.draft.snapshot.answers["patterns"] == ["full_lamp"]
    assert "Patterns: Full Lamp" in str(commit_render(panel).to_components())


async def test_door_size_may_omit_depth_but_not_width() -> None:
    services, drafts = setup("door")
    editor = await open_editor(services, drafts)
    event = press()
    await editor._edit_section("door_geometry", event)
    await submit_modal(event, {"opening_size": "2 x 3", "door_orientation": "door"})
    assert "opening_depth" not in editor.workspace.draft.snapshot.answers
    assert "Door size: 2 x 3 x ?" in rendered_text(editor)
    assert "Door size: 2 x 3 x ? —" not in rendered_text(editor)

    event = press()
    await editor._edit_section("door_geometry", event)
    await submit_modal(event, {"opening_size": "? x 3", "door_orientation": "door"})
    assert "Door size: ? x 3 x ? — ⚠️ Required" in rendered_text(editor)


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
    await editor.workspace.save_values({"opening_time": 7})
    assert "Opening time: 0\\.35s" in rendered_text(editor)
    event = press()
    await editor._edit_section("door_timing", event)
    entry = next(item for item in event.present_form.call_args.args[0].items if item.key == "opening_time")
    assert entry.format(entry.default) == "0.35s"
    assert _parse_ticks("0.35s") == 7


async def test_public_visibility_reveals_required_license_and_permission() -> None:
    services, drafts = setup()
    ws = (await open_editor(services, drafts)).workspace
    rights = ws.section("rights_privacy")
    assert rights is not None
    assert [field.id for field in rights.fields] == ["schematic_visibility"]
    await ws.save_values({"schematic_visibility": "public_download"})
    rights = ws.section("rights_privacy")
    assert rights is not None
    assert [field.id for field in rights.fields] == ["schematic_visibility", "schematic_license", "rights_attestation"]


async def test_picker_opens_on_the_page_of_the_current_choice() -> None:
    services, drafts = setup()
    # Web drafts are still asked for the exported version, which is a paged catalogue field.
    drafts.current = replace(drafts.current, origin=SubmissionOrigin.WEB)
    editor = await open_editor(services, drafts)
    # The stub catalogue lists Java 1.30 down to 1.1, so 1.2 is on the second page.
    await editor.workspace.save_values({"source_version": "Java 1.2"})
    child = SectionEditorScreen(editor.workspace, "dimensions_versions")
    text = rendered_text(child)
    assert "Page 2 of 2." in text
    assert "Choices on other pages are kept" not in text
    (select,) = find_all(child.render(), SelectMenu)
    assert [option.label for option in select.options if option.default] == ["Java 1.2"]
    await child._select_value("source_version", Mock(spec=sd.SelectionEvent, values=("Java 1.1",)))
    assert editor.workspace.draft.snapshot.answers["source_version"] == "Java 1.1"
    assert "Minecraft version: Java 1\\.1" in rendered_text(editor)


async def test_files_are_listed_with_a_status_only_when_they_need_attention() -> None:
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
    assert "**door\\.litematic** · Schematic\n" in text
    assert "**clip\\.mp4** · Video · ⚠️ Upload failed" in text
    assert "old" not in text

    files = FilesEditorScreen(editor.workspace)
    await files._select_file(Mock(spec=sd.SelectionEvent, values=(str(intake.files[0].id),)))
    assert {"Replace", "Remove", "Use as main schematic", "Add files", "Done"} <= set(labels(files.render()))


async def test_editor_messages_lock_once_the_draft_is_submitted() -> None:
    services, drafts = setup("door")
    editor = await open_editor(services, drafts)
    child = SectionEditorScreen(editor.workspace, "patterns")
    drafts.current = replace(drafts.current, snapshot=replace(drafts.current.snapshot, status=DraftStatus.PROCESSING))
    await editor.workspace.reload()
    assert "can no longer be edited" in rendered_text(child)
    assert not labels(child.render())


async def test_preview_draws_the_card_once_its_title_can_be_formed() -> None:
    services, drafts = setup("door")
    drawn: list[Build] = []

    async def render_card(build: Build) -> Any:
        drawn.append(build)
        return sl.paragraph("card")

    editor = await open_editor(services, drafts, render_card=render_card)
    preview = BuildPreviewScreen(editor.workspace)
    await preview.card.reload()
    assert "Fill in Door size, Door type to see the preview." in rendered_text(preview)

    await editor.workspace.save_values(
        {"opening_width": 2, "opening_height": 3, "door_orientation": "trapdoor", "patterns": ["full_lamp"]}
    )
    await preview.card.reload()
    assert "card" in rendered_text(preview)
    assert drawn[-1].patterns == ["Full Lamp"]
    assert drawn[-1].id is None


async def test_live_projection_moves_from_creating_to_done() -> None:
    services, drafts = setup()
    finalization = services.submission_finalization
    assert isinstance(finalization, Finalization)
    editor = await open_editor(services, drafts)
    drafts.current = replace(drafts.current, snapshot=replace(drafts.current.snapshot, status=DraftStatus.PROCESSING))
    await editor.workspace.projection.reload()
    assert editor.workspace.phase is Phase.CREATING
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
    await editor.workspace.reload()
    assert editor.workspace.phase is Phase.DONE
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
    assert editor.workspace.phase is Phase.NEEDS_CHANGES
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
    assert editor.workspace.phase is Phase.WAITING_FOR_FILES
    text = rendered_text(editor)
    assert "Waiting for your files" in text
    assert "still being processed" not in text
    assert "Refresh" in labels(editor.render())
    assert "Submit" not in labels(editor.render())


async def test_pressing_edit_on_a_catalogue_section_opens_a_second_message() -> None:
    services, drafts = setup("door")
    editor = await open_editor(services, drafts)
    async with sd.testing.stage(editor, owner=object()) as ui:
        harness = await ui.press("edit_patterns")

        (child,) = editor._screen_flow.children
        assert isinstance(child, SectionEditorScreen)
        # The child was sent as its own message; the panel kept its overview.
        harness.response.send_message.assert_awaited_once()
        assert harness.response.send_message.await_args is not None
        assert harness.response.send_message.await_args.kwargs["ephemeral"] is True
        harness.response.edit_message.assert_not_awaited()
        assert any("**Name and creators**" in text for text in ui.texts())
        assert any("**Patterns**" in text for text in ui.texts())
