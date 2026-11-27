"""Unit tests for the build log consent banner and its routed button."""

from dataclasses import dataclass
from typing import Any, cast, override
from unittest.mock import MagicMock

import discord
import pytest
from whenever import Instant

import squid_ui.ext.discord as sd
from squid.bot.routes import AppRequest
from squid.bot.submission.indexing import (
    BuildIndexingStickyMessage,
    disable_indexing,
    enable_indexing,
    open_indexing_settings,
)
from squid.bot.submission.message_intake import BuildMessageIntake
from squid.core.accounts.application import AccountService
from squid.core.accounts.domain import PRIVACY_NOTICE_VERSION, IdentityProvider, PrivacyNoticeReceipt
from squid.core.accounts.models import Account, AccountIdentity
from squid.core.actors import ActingContext, UserActor
from squid.core.settings.application import SettingsService
from squid.core.settings.domain.runtime import RuntimeSettings
from squid.core.submissions.errors import DraftCapacityExceededError
from squid_ui.ext.discord.testing import AsyncCallRecorder, InteractionHarness, MessageHarness
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services

BUILD_LOG_CHANNEL = 500
USER_ID = 42
AFTER_CUTOFF = Instant.from_utc(2026, 8, 5)


@pytest.fixture(autouse=True)
def _sticky_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the consent banner so these tests exercise its gated path."""
    monkeypatch.setattr("squid.bot.submission.message_intake.CONSENT_STICKY_ENABLED", True)


def _discord_account(*, account_id: int = 7, consented: bool) -> Account:
    return Account(
        id=account_id,
        created_at=AFTER_CUTOFF,
        discord_build_indexing_enabled=consented,
        identities=(AccountIdentity.discord(USER_ID),),
        consent=PrivacyNoticeReceipt(PRIVACY_NOTICE_VERSION, AFTER_CUTOFF) if consented else None,
    )


class AccountRecorder(AccountService):
    def __init__(self, account: Account | None) -> None:
        self.account = account
        self.reads: list[tuple[IdentityProvider, str]] = []

    @override
    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        self.reads.append((provider, subject))
        return self.account

    @override
    async def set_discord_build_indexing(self, discord_user_id: int, *, enabled: bool) -> None:
        assert discord_user_id == USER_ID
        self.account = _discord_account(consented=enabled)


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    @override
    async def get_locale(self, server_id: int, *, context: ActingContext) -> str | None:
        return None

    @override
    async def get_runtime(self, *, context: ActingContext) -> RuntimeSettings:
        return RuntimeSettings(build_log_channel_ids=(BUILD_LOG_CHANNEL,))


@dataclass(frozen=True)
class CommunityConfig:
    build_log_channel_ids: set[int]


class StickyRecorder(BuildIndexingStickyMessage):
    def __init__(self) -> None:
        self.triggers: list[discord.TextChannel] = []
        self.activity: list[int] = []

    @override
    async def trigger(self, channel: discord.TextChannel) -> None:
        self.triggers.append(channel)

    @override
    def record_activity(self, channel_id: int) -> None:
        self.activity.append(channel_id)


class InferenceBot:
    def __init__(self, accounts: AccountService) -> None:
        self.services = bot_services(settings=SettingsRecorder(), accounts=accounts)
        self.community_config = CommunityConfig(build_log_channel_ids={BUILD_LOG_CHANNEL})
        self.inference_model = "gpt-5.6-luna"
        self.inference_reasoning_effort = "low"
        self.catbox = object()

    async def refresh_posts(self, resource_kind: str, resource_key: str) -> None:
        assert resource_kind == "inference_run"


def _make_intake(*, account: Account | None) -> BuildMessageIntake[Any]:
    intake = BuildMessageIntake.__new__(BuildMessageIntake)
    intake.bot = cast(Any, InferenceBot(AccountRecorder(account)))
    intake.consent_sticky = StickyRecorder()
    return intake


def _make_message(*, author_id: int = USER_ID, channel_id: int = BUILD_LOG_CHANNEL) -> discord.Message:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.history = MagicMock(return_value=_empty_history())

    @dataclass(frozen=True)
    class Author:
        id: int
        bot: bool = False

    @dataclass(frozen=True)
    class Message:
        id: int
        author: Author
        channel: discord.TextChannel
        attachments: list[object]

    return cast(
        discord.Message,
        Message(id=100, author=Author(id=author_id), channel=channel, attachments=[]),
    )


async def _empty_history():
    if False:
        yield None


async def test_unknown_author_triggers_reminder_and_skips_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intake = _make_intake(account=None)
    message = _make_message()
    mock_ingest = AsyncCallRecorder()
    monkeypatch.setattr("squid.bot.submission.message_intake.ingest_message_bundle", mock_ingest)

    with ActingContext.direct(UserActor(discord_user_id=message.author.id)).scope():
        await intake.infer_build_from_message(message)

    assert isinstance(intake.consent_sticky, StickyRecorder)
    assert intake.consent_sticky.triggers == [message.channel]
    assert intake.consent_sticky.activity == []
    mock_ingest.assert_not_awaited()


async def test_enabled_indexing_records_activity_and_processes_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intake = _make_intake(account=_discord_account(consented=True))
    message = _make_message()
    mock_ingest = AsyncCallRecorder(result=[])
    monkeypatch.setattr("squid.bot.submission.message_intake.ingest_message_bundle", mock_ingest)

    with ActingContext.direct(UserActor(discord_user_id=message.author.id)).scope():
        await intake.infer_build_from_message(message)

    assert isinstance(intake.consent_sticky, StickyRecorder)
    assert intake.consent_sticky.triggers == []
    assert intake.consent_sticky.activity == [message.channel.id]
    mock_ingest.assert_awaited_once()


async def test_full_inference_intake_logs_without_replying_to_the_source_channel(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    intake = _make_intake(account=_discord_account(consented=True))
    message = _make_message()
    mock_ingest = AsyncCallRecorder(error=DraftCapacityExceededError(1_000))
    monkeypatch.setattr("squid.bot.submission.message_intake.ingest_message_bundle", mock_ingest)

    with ActingContext.direct(UserActor(discord_user_id=message.author.id)).scope():
        await intake.infer_build_from_message(message)

    send = cast(AsyncCallRecorder, message.channel.send)
    send.assert_not_awaited()
    assert "intake is full" in caplog.text
    assert f"message {message.id} in channel {message.channel.id}" in caplog.text


def _make_interaction(accounts: AccountService) -> Any:
    interaction = InteractionHarness(USER_ID, message_id=999)
    source = interaction.source
    source.client = make_layout_bot(
        services=bot_services(settings=SettingsRecorder(), accounts=accounts),
    )
    source.guild = None
    source.guild_id = None
    source.guild_locale = None
    source.locale = "en-US"
    interaction.followup.send.result = MessageHarness(message_id=999)
    return source


async def test_indexing_settings_are_personal() -> None:
    accounts = AccountRecorder(_discord_account(consented=True))
    interaction = _make_interaction(accounts)

    await open_indexing_settings(cast(AppRequest, await sd.request(cast(Any, interaction))))

    assert accounts.reads == [(IdentityProvider.DISCORD, str(USER_ID))]
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is True


@pytest.mark.parametrize("enabled", [False, True])
async def test_indexing_control_sets_explicit_value(enabled: bool) -> None:
    accounts = AccountRecorder(None)
    interaction = _make_interaction(accounts)
    handler = enable_indexing if enabled else disable_indexing
    await handler(cast(AppRequest, await sd.request(cast(Any, interaction))))
    assert accounts.account is not None
    assert accounts.account.discord_build_indexing_enabled is enabled


async def test_sticky_banner_uses_shared_composition_and_preserves_its_route(caplog: pytest.LogCaptureFixture) -> None:
    channel = MagicMock(spec=discord.TextChannel)
    payload = await StickyRecorder().render(channel)
    items = tuple(payload.layout.walk_children())
    descriptions = [item.content for item in items if isinstance(item, discord.ui.TextDisplay)]
    buttons = [item for item in items if isinstance(item, discord.ui.Button)]
    assert "Automatically index my Discord builds" in "\n".join(descriptions)
    assert "all participating Discord channels" in "\n".join(descriptions)
    assert len(buttons) == 1
    assert buttons[0].custom_id == open_indexing_settings.custom_id()
    assert buttons[0].style == discord.ButtonStyle.primary
    assert not any("layout degraded" in record.message for record in caplog.records)
