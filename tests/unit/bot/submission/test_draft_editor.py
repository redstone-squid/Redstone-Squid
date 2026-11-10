"""Reopen retained submission drafts and save answers against the displayed edit revision.

The editor uses the draft's pinned form schema; concurrent answer edits must not overwrite each other.
"""

from dataclasses import replace
from typing import override
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from whenever import Instant

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ui.drafts import DraftEditorScreen, draft_editor
from squid.runtime import BotServices
from squid.submissions.application import (
    AppliedDraftChange,
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
    DraftChange,
    DraftRevisionConflictError,
    DraftSnapshot,
    DraftStatus,
    FormManifest,
    SubmissionOrigin,
)
from squid_reactivity import LocalTopicBus
from squid_ui.testing import labels, texts
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


class Intake(SubmissionAttachmentIntake):
    def __init__(self) -> None:
        pass

    @override
    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[SuppliedAttachment, ...]:
        return ()


class Finalization(SubmissionFinalizationService):
    def __init__(self) -> None:
        pass

    @override
    async def status(self, draft_id: UUID, account_id: DraftActor) -> SubmissionRequestResult | None:
        return None


def setup() -> tuple[BotServices, Drafts]:
    now = Instant.now()
    draft = StoredDraft(
        snapshot=DraftSnapshot(uuid4(), 7, CURRENT_SUBMISSION_SCHEMA, CURRENT_SUBMISSION_SCHEMA_REVISION, "other"),
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


async def test_reopening_reads_saved_answers_and_pinned_manifest() -> None:
    services, drafts = setup()
    first = await draft_editor(
        services,
        7,
        drafts.current.snapshot.id,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
    )
    await first._save("display_name", "Saved name")
    reopened = await draft_editor(
        services,
        7,
        drafts.current.snapshot.id,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
    )
    assert reopened is not first
    assert reopened.draft.snapshot.answers["display_name"] == "Saved name"
    assert reopened.manifest.revision == reopened.draft.snapshot.schema_revision
    await reopened.projection.reload()
    assert "Submit for review" in labels(reopened.render())


async def test_staff_writes_use_actor_and_preserve_owner() -> None:
    services, drafts = setup()
    editor = await draft_editor(
        services,
        99,
        drafts.current.snapshot.id,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
    )
    await editor._save("description", "Staff correction")
    assert editor.draft.snapshot.owner_account_id == 7
    assert all(actor == 99 for actor in drafts.actors)
    assert drafts.changes[0].client_instance_id == "discord-editor"


async def test_stale_modal_does_not_overwrite_a_newer_edit() -> None:
    services, drafts = setup()
    editor = await draft_editor(
        services,
        7,
        drafts.current.snapshot.id,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
    )
    base_revision = editor.draft.snapshot.revision
    await editor._save("display_name", "Newer value")
    await editor._save("display_name", "Stale modal", base_revision=base_revision)
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
async def test_grouped_editor_uses_one_card_within_discord_limits(category: str) -> None:
    services, drafts = setup()
    drafts.current = replace(drafts.current, snapshot=replace(drafts.current.snapshot, category=category))
    editor = await draft_editor(
        services,
        7,
        drafts.current.snapshot.id,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
    )
    await editor.projection.reload()
    root = sd.MessageRoot(editor, access=sd.Owner(7), timeout=None)
    payload = commit_render(root).to_components()
    assert len(payload) == 1
    assert payload[0]["type"] == 17
    assert not payload_problems(payload)
    assert "Required fields still needed: Creators, Build width, Build height, Build depth" in "\n".join(
        texts(editor.render())
    )
    assert "Edit value" not in labels(editor.render())
    for group in editor.groups:
        if group.fields[0].option_source is not None:
            continue
        event = Mock(spec=sl.PressEvent, present_form=AsyncMock())
        await editor._edit_group(group.id, event)
        spec = event.present_form.call_args.args[0]
        modal = build_form_modal(spec, on_submit=AsyncMock())
        assert 1 <= len(modal.children) <= 5


async def test_dimension_modal_saves_together_and_rejects_stale_edits() -> None:
    services, drafts = setup()
    editor = await draft_editor(
        services,
        7,
        drafts.current.snapshot.id,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
    )
    event = Mock(spec=sl.PressEvent, present_form=AsyncMock())
    await editor._edit_group("dimensions_versions_0", event)
    spec = event.present_form.call_args.args[0]
    entries = {entry.key: entry for entry in spec.items}
    assert entries["capture_depth"].required
    assert not entries["version_compatibility"].required
    submit = event.present_form.call_args.kwargs["on_submit"]
    values = {"capture_width": 3, "capture_height": 4, "capture_depth": 5, "version_compatibility": None}
    await submit(Mock(spec=sl.SubmitEvent, values=values))
    assert len(drafts.changes) == 1
    assert editor.draft.snapshot.answers["capture_depth"] == 5
    await editor.projection.reload()
    assert "Build depth" not in texts(editor.render())[1]

    await submit(Mock(spec=sl.SubmitEvent, values={**values, "capture_width": 9, "capture_depth": 9}))
    assert editor.draft.snapshot.answers["capture_width"] == 3
    assert editor.draft.snapshot.answers["capture_depth"] == 5
    assert source_text(editor.notice) == DraftRevisionConflictError(expected=0, actual=1).public_detail()


async def test_public_visibility_reveals_required_license_and_permission() -> None:
    services, drafts = setup()
    editor = await draft_editor(
        services,
        7,
        drafts.current.snapshot.id,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
    )
    assert "rights_attestation" not in {field.id for group in editor.groups for field in group.fields}
    await editor._save("schematic_visibility", "public_download")
    rights = next(group for group in editor.groups if group.id == "rights_privacy_0")
    assert {field.id for field in rights.fields} == {"schematic_visibility", "schematic_license", "rights_attestation"}
    await editor.projection.reload()
    assert "Public schematic license" in texts(editor.render())[1]


async def test_live_projection_refreshes_saved_state() -> None:
    services, drafts = setup()
    editor = await draft_editor(
        services,
        7,
        drafts.current.snapshot.id,
        changes=LocalTopicBus().topic("submission_draft", str(drafts.current.snapshot.id)),
    )
    await editor.projection.reload()
    drafts.current = replace(drafts.current, snapshot=replace(drafts.current.snapshot, status=DraftStatus.SUBMITTED))
    await editor.projection.reload()
    assert editor.draft.snapshot.status is DraftStatus.SUBMITTED
    assert "Submit for review" not in labels(editor.render())
