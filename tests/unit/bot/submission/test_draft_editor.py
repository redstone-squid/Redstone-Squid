"""Persisted editor recovery and optimistic form writes."""

from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from whenever import Instant

from squid.bot.submission.ui.drafts import DraftEditorScreen, draft_editor
from squid.runtime import BotServices
from squid.submissions.application import StoredDraft, build_submission_manifest
from squid.submissions.domain import DraftChange, DraftRevisionConflictError, DraftSnapshot, SubmissionOrigin
from squid_ui.testing import labels


class Drafts:
    def __init__(self, draft: StoredDraft) -> None:
        self.current = draft
        self.actors: list[int] = []
        self.changes: list[DraftChange] = []

    async def get_accessible(self, draft_id: object, actor: int) -> StoredDraft:
        assert draft_id == self.current.snapshot.id
        self.actors.append(actor)
        return self.current

    async def apply_change(self, draft_id: object, actor: int, change: DraftChange, *, locale: str | None) -> None:
        self.actors.append(actor)
        self.changes.append(change)
        self.current = replace(self.current, snapshot=self.current.snapshot.apply(change))


class Forms:
    async def manifest_revision(self, schema_id: str, revision: int, *, locale: str | None) -> object:
        return build_submission_manifest(revision=revision)


class Intake:
    async def list(self, draft_id: object, actor: int) -> tuple[()]:
        return ()


class Finalization:
    async def status(self, draft_id: object, actor: int) -> None:
        return None


def setup() -> tuple[BotServices, Drafts]:
    now = Instant.now()
    draft = StoredDraft(
        snapshot=DraftSnapshot(uuid4(), 7, "build_submission.v1", 1, "other"),
        origin=SubmissionOrigin.DISCORD,
        created_at=now,
        updated_at=now,
        expires_at=now.add(days=7, days_assumed_24h_ok=True),
    )
    drafts = Drafts(draft)
    services = cast(
        BotServices,
        SimpleNamespace(
            submission_drafts=drafts,
            submission_forms=Forms(),
            submission_finalization=Finalization(),
            submission_intake=Intake(),
        ),
    )
    return services, drafts


async def test_reopening_reads_saved_answers_and_pinned_manifest() -> None:
    services, drafts = setup()
    first = await draft_editor(services, 7, drafts.current.snapshot.id)
    await first._save("display_name", "Saved name")
    reopened = await draft_editor(services, 7, drafts.current.snapshot.id)
    assert reopened is not first
    assert reopened.draft.snapshot.answers["display_name"] == "Saved name"
    assert reopened.manifest.revision == reopened.draft.snapshot.schema_revision
    assert "Submit for review" in labels(reopened.render())


async def test_staff_writes_use_actor_and_preserve_owner() -> None:
    services, drafts = setup()
    editor = await draft_editor(services, 99, drafts.current.snapshot.id)
    await editor._save("description", "Staff correction")
    assert editor.draft.snapshot.owner_account_id == 7
    assert all(actor == 99 for actor in drafts.actors)
    assert drafts.changes[0].client_instance_id == "discord-editor"


async def test_stale_modal_does_not_overwrite_a_newer_edit() -> None:
    services, drafts = setup()
    editor = await draft_editor(services, 7, drafts.current.snapshot.id)
    base_revision = editor.draft.snapshot.revision
    await editor._save("display_name", "Newer value")
    await editor._save("display_name", "Stale modal", base_revision=base_revision)
    assert editor.draft.snapshot.answers["display_name"] == "Newer value"
    assert editor.notice == DraftRevisionConflictError(expected=0, actual=1).public_detail()


def test_every_visible_field_comes_from_the_manifest() -> None:
    services, drafts = setup()
    manifest = build_submission_manifest()
    editor = DraftEditorScreen(services, 7, drafts.current, manifest)
    assert {field.id for field in editor.fields} <= {field.id for field in manifest.fields_for("other")}
    assert "source_version" in {field.id for field in editor.fields}
    assert "rights_attestation" not in {field.id for field in editor.fields}


async def test_manifest_pages_fit_discord_component_limits() -> None:
    import squid_ui_discord as sd
    from squid_ui_discord.testing import commit_render

    services, drafts = setup()
    editor = await draft_editor(services, 7, drafts.current.snapshot.id)
    root = sd.MessageRoot(editor, access=sd.Owner(7), timeout=None)
    commit_render(root)
    editor.selected = "creators"
    commit_render(root)
