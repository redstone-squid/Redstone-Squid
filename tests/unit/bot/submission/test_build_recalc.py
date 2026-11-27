"""The "Scan Build Message" context menu: who may run it, and on what."""

from dataclasses import dataclass
from typing import Any, Literal, cast, override
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import discord
import pytest
from whenever import Instant

import squid_ui.ext.discord as sd
from squid.bot.submission.cog import BuildCommands
from squid.bot.submission.indexing import BuildIndexingStickyMessage
from squid.bot.submission.message_intake import BuildMessageIntake
from squid.bot.utils.permissions import PermissionNodeRequired
from squid.core.accounts.application import AccountService
from squid.core.accounts.domain import IdentityProvider, PrivacyNoticeReceipt
from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, Actor, UserActor
from squid.core.builds.application.inference import BuildInferenceInput
from squid.core.builds.domain import BuildCategory, BuildInput
from squid.core.permissions.application import PermissionService
from squid.core.permissions.application.ports import PermissionUnitOfWork
from squid.core.permissions.domain import CheckMode, Decision, PermissionCheck, PermissionNode, Reason
from squid.core.permissions.domain.models import RolePermissionSubject
from squid.core.settings.application import SettingsService
from squid.core.settings.domain.runtime import RuntimeSettings
from squid.core.submissions.application.inference_runs import InferenceCandidate, SubmissionInferenceRuns
from squid.core.submissions.application.recalculation import RecalculationProposalService
from squid.core.submissions.application.recalculation_values import RecalculationFacts, RecalculationProposal
from squid.core.submissions.domain.source_files import SubmissionSourceFile
from squid_ui.ext.discord.testing import (
    AsyncCallRecorder,
    ContextHarness,
    InteractionHarness,
    MessageHarness,
    invoke_context_menu,
)
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services

BUILD_LOG_CHANNEL = 500


@pytest.fixture(autouse=True)
def _sticky_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the consent banner so these tests exercise its gated path."""
    monkeypatch.setattr("squid.bot.submission.message_intake.CONSENT_STICKY_ENABLED", True)


class StubPermissions(PermissionService):
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed

    @override
    async def check(
        self,
        subject: Actor | RolePermissionSubject,
        *nodes: PermissionNode | str,
        uow: PermissionUnitOfWork | None = None,
        mode: CheckMode = "all",
    ) -> PermissionCheck:
        return PermissionCheck(
            tuple(Decision(str(node), allowed=self.allowed, reason=Reason.DEFAULT) for node in nodes), mode=mode
        )


class AccountRecorder(AccountService):
    def __init__(self, account: Account | None) -> None:
        self.account = account

    @override
    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        return self.account

    @override
    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: PrivacyNoticeReceipt | None = None
    ) -> Account:
        assert self.account is not None
        return self.account


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    @override
    async def get_locale(self, server_id: int, *, context: ActingContext) -> str | None:
        return None

    @override
    async def get_runtime(self, *, context: ActingContext) -> RuntimeSettings:
        return RuntimeSettings(build_log_channel_ids=(BUILD_LOG_CHANNEL,))


class AccountIdResolver:
    async def resolve(self, accounts: AccountService, discord_id: int) -> int | None:
        return 1


class OwnerCheck:
    async def __call__(self, user: object) -> bool:
        return False


@dataclass(frozen=True)
class CommunityConfig:
    build_log_channel_ids: set[int]


class IndexingStickyRecorder(BuildIndexingStickyMessage):
    def __init__(self) -> None:
        self.calls: list[discord.TextChannel] = []

    @override
    async def trigger(self, channel: discord.TextChannel) -> None:
        self.calls.append(channel)


class RecordingMessageIntake(BuildMessageIntake[Any]):
    def __init__(self) -> None:
        self.inferred: list[discord.Message] = []

    @override
    async def propose_recalculation(self, request: Any, message: discord.Message, *, owner_account_id: int) -> Any:
        from squid.bot.ui import text_node

        self.inferred.append(message)
        return text_node("Review the recalculation proposal.")


def _intake(*, allowed: bool = True, indexing_enabled: bool = True) -> RecordingMessageIntake:
    intake = RecordingMessageIntake()
    account = Account(id=1, discord_build_indexing_enabled=indexing_enabled)

    intake.bot = cast(
        Any,
        make_layout_bot(
            services=bot_services(
                settings=SettingsRecorder(),
                accounts=AccountRecorder(account),
                permissions=StubPermissions(allowed=allowed),
            ),
            account_ids=AccountIdResolver(),
            is_owner=OwnerCheck(),
            community_config=CommunityConfig(build_log_channel_ids={BUILD_LOG_CHANNEL}),
        ),
    )
    intake.indexing_sticky = IndexingStickyRecorder()
    return intake


def _interaction() -> discord.Interaction[Any]:
    interaction = InteractionHarness(user_id=7).source
    interaction.guild = None
    interaction.guild_id = None
    interaction.guild_locale = None
    interaction.locale = "en-US"
    interaction.client = None
    return cast(discord.Interaction[Any], interaction)


def _message(*, channel_id: int = BUILD_LOG_CHANNEL, from_bot: bool = False, author_id: int = 42) -> discord.Message:
    # Use a TextChannel spec so the channel eligibility isinstance check succeeds.
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id

    @dataclass(frozen=True)
    class Author:
        id: int
        bot: bool

    @dataclass(frozen=True)
    class Message:
        author: Author
        channel: discord.TextChannel

    return cast(discord.Message, Message(author=Author(id=author_id, bot=from_bot), channel=channel))


async def _run(intake: BuildMessageIntake[Any], message: discord.Message) -> discord.Interaction[Any]:
    interaction = _interaction()
    cast(Any, interaction).client = intake.bot
    cog = BuildCommands.__new__(BuildCommands)
    cog.bot = intake.bot
    cog.ui = intake.bot.ui.scope(cog)
    cog.intake = intake
    with ActingContext.direct(UserActor(discord_user_id=interaction.user.id)).scope():
        await invoke_context_menu(cog, cog.recalc_context_menu, interaction, message)
    return interaction


async def test_the_menu_denies_the_way_the_command_did() -> None:
    """Context-menu denials use the same PermissionNodeRequired error as command checks."""
    intake = _intake(allowed=False)

    with pytest.raises(PermissionNodeRequired) as denial:
        await _run(intake, _message())

    assert denial.value.nodes == ("build.submission.recalc",)
    assert intake.inferred == []


async def test_a_message_no_build_can_come_from_says_so() -> None:
    """An ineligible channel gets a reply without starting inference."""
    intake = _intake()

    interaction = await _run(intake, _message(channel_id=999))

    assert intake.inferred == []
    assert cast(Any, interaction).edit_original_response.await_count == 1


async def test_a_build_log_message_is_recalculated() -> None:
    intake = _intake()
    message = _message()

    await _run(intake, message)

    assert intake.inferred == [message]


async def test_author_can_scan_one_message_with_automatic_indexing_off() -> None:
    intake = _intake(indexing_enabled=False)
    message = _message(author_id=7)
    await _run(intake, message)
    assert intake.inferred == [message]


async def test_recalc_requires_other_authors_to_enable_indexing() -> None:
    intake = _intake(indexing_enabled=False)
    message = _message()

    interaction = await _run(intake, message)

    assert intake.inferred == []
    assert cast(Any, interaction).edit_original_response.await_count == 1
    assert isinstance(intake.indexing_sticky, IndexingStickyRecorder)
    assert intake.indexing_sticky.calls == [message.channel]


class RetainedInference(SubmissionInferenceRuns):
    def __init__(self, candidate: InferenceCandidate) -> None:
        self.infer_calls: AsyncCallRecorder = AsyncCallRecorder(result=(candidate,))

    @override
    async def infer(
        self,
        run_id: UUID,
        owner: int,
        bundle: BuildInferenceInput,
        *,
        model: str,
        reasoning_effort: str | None = None,
        source_files: tuple[SubmissionSourceFile, ...] = (),
        purpose: Literal["submission", "recalculation"] = "submission",
        context: ActingContext,
    ) -> tuple[InferenceCandidate, ...]:
        return await self.infer_calls(
            run_id,
            owner,
            bundle,
            model=model,
            reasoning_effort=reasoning_effort,
            source_files=source_files,
            purpose=purpose,
        )


class Proposals(RecalculationProposalService):
    def __init__(self, proposal: RecalculationProposal) -> None:
        self.create_calls: AsyncCallRecorder = AsyncCallRecorder(result=proposal)

    @override
    async def create(
        self,
        *,
        context: ActingContext,
        run_id: UUID,
        index: int,
        owner_account_id: int,
        source_message_id: int,
        candidate: BuildInput,
    ) -> RecalculationProposal:
        return await self.create_calls(
            run_id=run_id,
            index=index,
            owner_account_id=owner_account_id,
            actor=context.effective,
            source_message_id=source_message_id,
            candidate=candidate,
        )


async def test_production_recalculation_retains_candidates_without_submitting(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = BuildInput(category=BuildCategory.DOOR, width=9)
    run_id = uuid4()
    inference = RetainedInference(InferenceCandidate(uuid4(), run_id, 1, candidate))
    now = Instant.now()
    proposal = RecalculationProposal(
        uuid4(), run_id, 1, 7, 123, RecalculationFacts.from_draft(candidate), None, None, {}, {}, now, now.add(hours=1)
    )
    proposals = Proposals(proposal)
    intake = _intake()
    intake.bot.services = bot_services(submission_inference=inference, submission_recalculations=proposals)
    intake.bot.inference_model = "test-model"
    intake.bot.inference_reasoning_effort = None
    monkeypatch.setattr(
        "squid.bot.submission.message_intake.assemble_bundle", AsyncCallRecorder(result="retained-input")
    )
    monkeypatch.setattr(intake.bot.actors, "resolve", AsyncCallRecorder(result=UserActor(account_id=7)))
    context = ContextHarness(bot=intake.bot).source
    context.interaction = InteractionHarness(client=intake.bot).source
    context.interaction.id = 55
    request = await sd.request(context)
    result = await BuildMessageIntake.propose_recalculation(
        intake,
        request,
        MessageHarness(message_id=123).source,
        owner_account_id=1,
    )
    assert result is not None
    assert proposals.create_calls.await_args.kwargs["candidate"] is candidate
    assert proposals.create_calls.await_args.kwargs["owner_account_id"] == 1
    assert proposals.create_calls.await_args.kwargs["source_message_id"] == 123
