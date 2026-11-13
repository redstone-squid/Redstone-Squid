"""`/build submit` as one request: the private defer is completed by the workspace."""

from collections.abc import Iterable
from dataclasses import replace
from typing import Any, cast, override
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import discord
import pytest
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import CURRENT_CONSENT_VERSION, AccountConsent, IdentityProvider
from squid.accounts.models import Account
from squid.bot.submission.submit import BuildSubmitCommands, SavedDraftsScreen
from squid.bot.submission.ui import controls as build_controls
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.bot.utils.permissions import AccountIdCache
from squid.builds.application import BuildService
from squid.core.actors import Actor
from squid.messages.domain import MessageSnapshot
from squid.permissions.application import PermissionService, SubjectRecords
from squid.permissions.domain import Effect
from squid.permissions.models import PermissionGrant
from squid.schematics.application.services import SchematicService
from squid.settings.application import SettingsService
from squid.submissions.application import (
    AppliedDraftChange,
    CheckedInFormManifestRegistry,
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
from squid.submissions.domain import DraftChange, DraftSnapshot, DraftStatus, FormManifest, SubmissionOrigin
from squid.submissions.domain.source_files import SubmissionSourceFile
from squid.submissions.errors import DraftAccessDeniedError
from squid_ui.text import source_text
from squid_ui_discord.testing import InteractionHarness, iter_component_payloads, stage
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services


class AccountRecorder(AccountService):
    def __init__(self) -> None:
        self.account = Account(
            id=1,
            created_at=Instant.from_utc(2026, 8, 29),
            consent=AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 29)),
        )

    @override
    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        return self.account


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    @override
    async def get_locale(self, server_id: int) -> str | None:
        return None


class BuildRecorder(BuildService):
    def __init__(self) -> None:
        pass


class Schematics(SchematicService):
    def __init__(self) -> None:
        pass

    @property
    @override
    def available(self) -> bool:
        return False


class Forms(SubmissionFormService):
    def __init__(self) -> None:
        pass

    @override
    async def manifest(self, *, locale: str | None) -> FormManifest:
        return build_submission_manifest()

    @override
    async def manifest_revision(self, schema_id: str, revision: int, *, locale: str | None) -> FormManifest | None:
        return build_submission_manifest(revision=revision)

    @override
    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        return FormOptionSet(source, category, 1, ())


class Drafts(SubmissionDraftService):
    def __init__(self) -> None:
        self.current: StoredDraft | None = None

    @override
    async def create(
        self,
        *,
        owner_account_id: int,
        category: str,
        origin: SubmissionOrigin,
        client_capabilities: frozenset[str],
        locale: str | None,
        source_installation_id: UUID | None = None,
        now: Instant | None = None,
        draft_id: UUID | None = None,
        inferred: bool = False,
        source_messages: tuple[MessageSnapshot, ...] = (),
        source_files: tuple[SubmissionSourceFile, ...] = (),
        source_issues: tuple[str, ...] = (),
        inference_run_id: UUID | None = None,
    ) -> StoredDraft:
        assert draft_id is not None
        created = Instant.now()
        self.current = StoredDraft(
            snapshot=DraftSnapshot(
                draft_id,
                owner_account_id,
                CURRENT_SUBMISSION_SCHEMA,
                CURRENT_SUBMISSION_SCHEMA_REVISION,
                category,
            ),
            origin=origin,
            created_at=created,
            updated_at=created,
            expires_at=created.add(days=7, days_assumed_24h_ok=True),
        )
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
        assert self.current is not None
        self.current = replace(self.current, snapshot=self.current.snapshot.apply(change))
        return AppliedDraftChange(self.current)

    @override
    async def get_accessible(self, draft_id: UUID, actor: DraftActor) -> StoredDraft:
        assert self.current is not None
        return self.current


class Finalization(SubmissionFinalizationService):
    def __init__(self) -> None:
        pass

    @override
    async def status(self, draft_id: UUID, account_id: DraftActor) -> SubmissionRequestResult | None:
        return None


class Intake(SubmissionAttachmentIntake):
    def __init__(self) -> None:
        pass

    @override
    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[SuppliedAttachment, ...]:
        return ()


def _cog() -> BuildSubmitCommands[Any]:
    cog = BuildSubmitCommands.__new__(BuildSubmitCommands)
    cog.bot = cast(
        Any,
        make_layout_bot(
            services=bot_services(
                settings=SettingsRecorder(),
                accounts=AccountRecorder(),
                schematics=Schematics(),
                submission_forms=Forms(),
                submission_drafts=Drafts(),
                submission_finalization=Finalization(),
                submission_intake=Intake(),
            )
        ),
    )
    cog.bot.is_owner = AsyncMock(return_value=False)
    cog.ui = cog.bot.ui.scope(cog)
    cog.builds = BuildRecorder()
    return cog


async def _submit(cog: BuildSubmitCommands[Any], **options: Any) -> InteractionHarness:
    harness = InteractionHarness(user_id=7, client=cog.bot)
    cast(Any, harness).id = 123
    harness.guild = None
    await cast(Any, BuildSubmitCommands).submit_form.callback(cog, harness.source, **options)
    return harness


def _screen(harness: InteractionHarness) -> Any:
    call = harness.edit_original_response.await_args
    assert call is not None
    return getattr(getattr(call.kwargs["view"], "_root", None), "component", None)


async def test_the_workspace_completes_the_private_defer() -> None:
    """Complete the existing private defer so no thinking placeholder or extra follow-up remains."""
    harness = await _submit(_cog(), door_size="2x2")

    harness.response.defer.assert_awaited_once()
    assert harness.response.defer.await_args is not None
    assert harness.response.defer.await_args.kwargs["ephemeral"] is True
    harness.followup.send.assert_not_awaited()
    assert isinstance(_screen(harness), DraftEditorScreen)


async def test_bad_dimensions_are_refused_in_place() -> None:
    harness = await _submit(_cog(), door_size="two by two")

    harness.followup.send.assert_not_awaited()
    harness.edit_original_response.assert_awaited_once()
    assert _screen(harness) is None


async def test_values_the_form_rejects_are_named_instead_of_dropped() -> None:
    # "5 wide" marks height and depth as not applicable (-1), which the form's minimum of 1 rejects.
    harness = await _submit(_cog(), door_size="5 wide", build_size="5x7x4")

    screen = _screen(harness)
    assert isinstance(screen, DraftEditorScreen)
    assert screen.workspace.draft.snapshot.answers["opening_width"] == 5
    assert "opening_height" not in screen.workspace.draft.snapshot.answers
    assert source_text(screen.workspace.notice) == (
        "Some values from the command could not be used: Door size. Fill them in below."
    )


@pytest.mark.parametrize("category", ["Door", "Extender", "Utility", "Entrance", "Other"])
async def test_submit_opens_the_selected_category_with_shared_prefills(category: str) -> None:
    harness = await _submit(_cog(), category=category, name="My build", description="Details", build_size="5x7x4")
    screen = _screen(harness)
    assert isinstance(screen, DraftEditorScreen)
    snapshot = screen.workspace.draft.snapshot
    assert snapshot.category == category.lower()
    assert snapshot.answers["display_name"] == "My build"
    assert snapshot.answers["description"] == "Details"
    assert snapshot.answers["capture_width"] == 5
    if category != "Door":
        assert "opening_width" not in snapshot.answers


@pytest.mark.parametrize(
    "options", [{"category": "Extender", "door_size": "2x2"}, {"category": "Utility", "patterns": "Regular"}]
)
async def test_submit_rejects_inapplicable_prefills(options: dict[str, str]) -> None:
    cog = _cog()
    harness = await _submit(cog, **options)
    assert _screen(harness) is None
    harness.edit_original_response.assert_awaited_once()


@pytest.mark.parametrize("inbox", [True, False])
async def test_saved_drafts_preserves_bot_owner_authority(inbox: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    cog = _cog()
    cog.bot.account_ids = AccountIdCache()
    cog.bot.is_owner = AsyncMock(return_value=True)
    attention = AsyncMock(return_value=())
    own = AsyncMock(return_value=())
    inference = AsyncMock(return_value=())
    monkeypatch.setattr(cog.bot.services.submission_drafts, "attention_inbox", attention)
    monkeypatch.setattr(cog.bot.services.submission_drafts, "list_active", own)
    cog.bot.services = replace(cog.bot.services, submission_inference=AsyncMock(list_active=inference))
    harness = InteractionHarness(user_id=7, client=cog.bot)
    harness.guild = None

    await cast(Any, BuildSubmitCommands).saved_drafts.callback(cog, harness.source, inbox=inbox)

    actor = Actor(account_id=1, discord_user_id=7, is_bot_owner=True)
    if inbox:
        attention.assert_awaited_once_with(actor, limit=10)
        own.assert_not_awaited()
    else:
        own.assert_awaited_once_with(1)
        attention.assert_not_awaited()
    inference.assert_awaited_once_with(actor, inbox=inbox)


@pytest.mark.parametrize("inbox", [True, False])
@pytest.mark.parametrize(("draft_count", "run_count"), [(20, 0), (0, 20), (10, 10)])
async def test_saved_drafts_paginate_all_entries(
    inbox: bool, draft_count: int, run_count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = _cog()
    cog.bot.account_ids = AccountIdCache()
    cog.bot.is_owner = AsyncMock(return_value=True)
    now = Instant.now()
    drafts = tuple(
        StoredDraft(
            snapshot=DraftSnapshot(
                UUID(int=index + 1), 1, CURRENT_SUBMISSION_SCHEMA, CURRENT_SUBMISSION_SCHEMA_REVISION, "door"
            ),
            origin=SubmissionOrigin.DISCORD,
            created_at=now,
            updated_at=now,
            expires_at=now.add(days=7, days_assumed_24h_ok=True),
        )
        for index in range(draft_count)
    )
    runs = tuple(UUID(int=index + 100) for index in range(run_count))
    monkeypatch.setattr(cog.bot.services.submission_drafts, "attention_inbox", AsyncMock(return_value=drafts))
    monkeypatch.setattr(cog.bot.services.submission_drafts, "list_active", AsyncMock(return_value=drafts))
    cog.bot.services = replace(
        cog.bot.services, submission_inference=AsyncMock(list_active=AsyncMock(return_value=runs))
    )
    harness = InteractionHarness(user_id=7, client=cog.bot)
    harness.guild = None

    await cast(Any, BuildSubmitCommands).saved_drafts.callback(cog, harness.source, inbox=inbox)

    screen = _screen(harness)
    assert isinstance(screen, SavedDraftsScreen)
    expected = {
        *(build_controls.reopen_draft.id(draft_id=str(draft.snapshot.id)) for draft in drafts),
        *(build_controls.reopen_inference.id(run_id=str(run_id)) for run_id in runs),
    }
    seen: list[str] = []
    async with stage(SavedDraftsScreen(screen.nodes), owner=object(), user_id=7) as ui:
        while True:
            ui.assert_within_limits()
            view = ui.root._candidates.view
            assert view is not None
            seen.extend(
                route
                for item in iter_component_payloads(view.to_components())
                if (route := item.get("custom_id")) in expected
            )
            next_key = ui.key_for("__cursor_next.saved-drafts")
            if ui.control(next_key).payload.get("disabled"):
                break
            await ui.press(next_key)
        assert len(seen) == len(expected)
        assert set(seen) == expected


@pytest.mark.parametrize("bot_owner", [True, False], ids=["bot-owner", "discord-role"])
async def test_staff_inbox_item_reopens_with_real_permission_checks(bot_owner: bool) -> None:
    cog = _cog()
    submitted = _screen(await _submit(cog))
    assert isinstance(submitted, DraftEditorScreen)
    draft = submitted.workspace.draft
    draft = replace(draft, snapshot=replace(draft.snapshot, owner_account_id=99, status=DraftStatus.NEEDS_ATTENTION))
    grant = PermissionGrant(
        pattern="build.submission.edit", effect=int(Effect.ALLOW), subject_role_id=789, subject_guild_id=456
    )

    async def records(
        *, account_id: int | None, discord_role_ids: Iterable[int], guild_id: int | None
    ) -> SubjectRecords:
        return SubjectRecords(epoch=1, grants=(grant,) if guild_id == 456 and 789 in discord_role_ids else ())

    # Only persistence is mocked: the draft service and permission resolver enforce access.
    uow = AsyncMock()
    uow.__aenter__.return_value = uow
    uow.permissions.load_for_subject.side_effect = records
    uow.submission_drafts.get.return_value = draft
    uow.submission_drafts.list_attention.return_value = (draft,)
    permissions = PermissionService(uow_factory=lambda: uow)
    drafts = SubmissionDraftService(CheckedInFormManifestRegistry(), permissions=permissions, uow_factory=lambda: uow)
    cog.bot.services = replace(
        cog.bot.services,
        submission_drafts=drafts,
        submission_inference=AsyncMock(list_active=AsyncMock(return_value=())),
    )
    cog.bot.account_ids = AccountIdCache()
    cog.bot.is_owner = AsyncMock(return_value=bot_owner)
    guild = Mock(id=456, owner_id=999)
    member = Mock(spec=discord.Member, id=7, roles=[] if bot_owner else [Mock(id=789)], guild=guild)
    member.guild_permissions = discord.Permissions.none()
    guild.fetch_member = AsyncMock(return_value=member)
    inbox = InteractionHarness(user_id=7, client=cog.bot)
    inbox.user = member
    inbox.guild = guild
    await cast(Any, BuildSubmitCommands).saved_drafts.callback(cog, inbox.source, inbox=True)
    assert isinstance(_screen(inbox), SavedDraftsScreen)

    opened = InteractionHarness(user_id=7, client=cog.bot)
    opened.user = member
    opened.guild = guild
    await build_controls.reopen_draft(opened.source, str(draft.snapshot.id))
    opened.response.send_message.assert_awaited_once()
    call = opened.response.send_message.await_args
    assert call is not None
    assert call.kwargs["ephemeral"] is True
    editor = call.kwargs["view"]._root.component
    assert isinstance(editor, DraftEditorScreen)
    assert editor.workspace.draft.snapshot.owner_account_id == 99
    assert await editor.workspace.may_edit()

    # Resolving later actions must use the new member roles and bot ownership.
    cog.bot.is_owner.return_value = False
    member.roles = []
    assert not await editor.workspace.may_edit()
    with pytest.raises(DraftAccessDeniedError):
        await build_controls.reopen_draft(opened.source, str(draft.snapshot.id))
