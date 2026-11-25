"""Browse and reopen saved drafts through the Discord command and routed buttons."""

from typing import override
from unittest.mock import AsyncMock
from uuid import UUID

import discord
import pytest
from whenever import Instant

from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.bot.submission.ui.inference import reopen_inference
from squid.core.accounts.domain import AccountConsent
from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, Actor
from squid.core.permissions.application import PermissionService, SubjectRecords
from squid.core.permissions.domain import Effect
from squid.core.permissions.models import PermissionGrant
from squid.core.submissions.application import CheckedInFormManifestRegistry, StoredDraft, SubmissionDraftService
from squid.core.submissions.application.forms import CURRENT_SUBMISSION_SCHEMA, CURRENT_SUBMISSION_SCHEMA_REVISION
from squid.core.submissions.application.inference_runs import SubmissionInferenceRuns
from squid.core.submissions.domain import DraftSnapshot, DraftStatus, SubmissionOrigin
from tests.e2e.discord.environment import command_environment, displayed_text
from tests.e2e.discord.workspace_services import WorkspaceAccounts
from tests.support.services import bot_services
from tests.support.submission_form import Finalization, Forms, Intake

EXTENSION = "squid.bot.submission.cog"


class DraftListing(SubmissionDraftService):
    def __init__(self, drafts: tuple[StoredDraft, ...] = ()) -> None:
        self.drafts = drafts
        self.owners: list[int] = []
        self.staff: list[Actor] = []

    @override
    async def list_active(self, account_id: int, *, limit: int = 10) -> tuple[StoredDraft, ...]:
        self.owners.append(account_id)
        return self.drafts

    @override
    async def attention_inbox(
        self, *, context: ActingContext, after: UUID | None = None, limit: int = 20
    ) -> tuple[StoredDraft, ...]:
        self.staff.append(context.effective)
        return self.drafts


class InferenceListing(SubmissionInferenceRuns):
    def __init__(self, runs: tuple[UUID, ...] = ()) -> None:
        self.runs = runs
        self.requests: list[tuple[Actor, bool]] = []

    @override
    async def list_active(self, *, context: ActingContext, inbox: bool = False) -> tuple[UUID, ...]:
        self.requests.append((context.effective, inbox))
        return self.runs


def saved_draft(identifier: int, *, account_id: int = 1, status: DraftStatus = DraftStatus.EDITING) -> StoredDraft:
    now = Instant.now()
    return StoredDraft(
        snapshot=DraftSnapshot(
            UUID(int=identifier),
            account_id,
            CURRENT_SUBMISSION_SCHEMA,
            CURRENT_SUBMISSION_SCHEMA_REVISION,
            "door",
            status=status,
        ),
        origin=SubmissionOrigin.DISCORD,
        created_at=now,
        updated_at=now,
        expires_at=now.add(days=7, days_assumed_24h_ok=True),
    )


@pytest.mark.parametrize("inbox", [True, False])
async def test_saved_drafts_preserves_bot_owner_authority(inbox: bool) -> None:
    accounts, drafts, runs = WorkspaceAccounts(), DraftListing(), InferenceListing()
    services = bot_services(accounts=accounts, submission_drafts=drafts, submission_inference=runs)
    async with command_environment(EXTENSION, services=services) as (bot, env):
        owner = env.create_user("owner")
        bot.owner_id = owner.id
        accounts.discord_accounts[str(owner.id)] = Account(id=1, consent=AccountConsent.grant_current())

        result = await owner.slash("build drafts", inbox=inbox)

        actor = Actor(account_id=1, discord_user_id=owner.id, is_bot_owner=True)
        assert drafts.staff == ([actor] if inbox else [])
        assert drafts.owners == ([] if inbox else [1])
        assert runs.requests == [(actor, inbox)]
        assert result.response is not None
        assert result.response.ephemeral


@pytest.mark.parametrize("inbox", [True, False])
@pytest.mark.parametrize(("draft_count", "run_count"), [(20, 0), (0, 20), (10, 10)])
async def test_saved_drafts_paginate_all_entries(inbox: bool, draft_count: int, run_count: int) -> None:
    drafts = DraftListing(tuple(saved_draft(i + 1) for i in range(draft_count)))
    runs = InferenceListing(tuple(UUID(int=i + 100) for i in range(run_count)))
    accounts = WorkspaceAccounts()
    services = bot_services(accounts=accounts, submission_drafts=drafts, submission_inference=runs)
    expected = {
        *(DraftEditorScreen.open_draft.custom_id(draft_id=draft.snapshot.id) for draft in drafts.drafts),
        *(reopen_inference.custom_id(run_id=run_id) for run_id in runs.runs),
    }
    async with command_environment(EXTENSION, services=services) as (bot, env):
        author = env.guild.add_member(env.create_user("author"))
        accounts.discord_accounts[str(author.id)] = Account(id=1, consent=AccountConsent.grant_current())
        bot.owner_id = author.id
        channel = env.guild.create_text_channel("submissions")
        result = await author.slash(channel, "build drafts", inbox=inbox)

        assert result.response is not None
        assert result.response.ephemeral
        message = result.response.message
        seen: list[str] = []
        for _ in range(len(expected)):
            view = discord.ui.LayoutView.from_message(message)
            buttons = [child for child in view.walk_children() if isinstance(child, discord.ui.Button)]
            seen.extend(button.custom_id for button in buttons if button.custom_id in expected)
            (forward,) = (button for button in buttons if button.label == "Next")
            if forward.disabled:
                break
            assert forward.custom_id is not None
            await author.click(message, custom_id=forward.custom_id)
            message = next(item for item in channel.history(viewer=author) if item.id == message.id)
        assert len(seen) == len(expected)
        assert set(seen) == expected


@pytest.mark.parametrize("bot_owner", [True, False], ids=["bot-owner", "discord-role"])
async def test_staff_loses_access_to_an_inbox_draft_after_authority_is_revoked(bot_owner: bool) -> None:
    accounts = WorkspaceAccounts()
    draft = saved_draft(1, account_id=99, status=DraftStatus.NEEDS_ATTENTION)
    uow = AsyncMock()
    uow.__aenter__.return_value = uow
    uow.submission_drafts.get.return_value = draft
    uow.submission_drafts.list_attention.return_value = (draft,)
    permissions = PermissionService(uow_factory=lambda: uow)
    drafts = SubmissionDraftService(CheckedInFormManifestRegistry(), permissions=permissions, uow_factory=lambda: uow)
    services = bot_services(
        accounts=accounts,
        permissions=permissions,
        submission_drafts=drafts,
        submission_inference=InferenceListing(),
        submission_forms=Forms(),
        submission_intake=Intake(),
        submission_finalization=Finalization(),
    )
    async with command_environment(EXTENSION, services=services) as (bot, env):
        guild = env.guild
        role = guild.create_role("Draft editors")
        user = env.create_user("reviewer")
        reviewer = guild.add_member(user, roles=[] if bot_owner else [role])
        accounts.discord_accounts[str(user.id)] = Account(id=1, consent=AccountConsent.grant_current())
        bot.owner_id = user.id if bot_owner else None
        grant = PermissionGrant(
            pattern="build.submission.edit",
            effect=int(Effect.ALLOW),
            subject_role_id=role.id,
            subject_guild_id=guild.id,
        )

        async def records(subject: Actor) -> SubjectRecords:
            allowed = subject.guild_id == guild.id and role.id in subject.discord_role_ids
            return SubjectRecords(subject=subject, epoch=1, grants=(grant,) if allowed else ())

        uow.permissions.load_for_subject.side_effect = records
        channel = guild.create_text_channel("submissions")
        listed = await reviewer.slash(channel, "build drafts", inbox=True)
        assert listed.response is not None
        assert listed.response.ephemeral
        opened = await reviewer.click(listed.response.message, label="Open")
        assert opened.response is not None
        assert opened.response.ephemeral
        assert any("Build draft" in line for line in displayed_text(opened.response.message))

        bot.owner_id = env.create_user("new owner").id
        if not bot_owner:
            guild.remove_member(reviewer)
            reviewer = guild.add_member(user)
        denied = await reviewer.click(listed.response.message, label="Open")

        assert denied.response is not None
        assert denied.response.ephemeral
        text = "\n".join(displayed_text(denied.response.message))
        assert "This action requires build.submission.edit" in text
        assert "account_id" not in text
        assert "discord_user_id" not in text
