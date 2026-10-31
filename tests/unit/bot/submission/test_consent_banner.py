"""Unit tests for the build log consent banner and its routed button."""

from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import CURRENT_CONSENT_VERSION, Account, AccountConsent, AccountIdentity, IdentityProvider
from squid.bot.consent import ConsentPrompt
from squid.bot.submission.consent_banner import (
    BuildLogConsentStickyMessage,
    open_consent_prompt,
)
from squid.bot.submission.submit import BuildSubmitCommands
from squid.settings.application import SettingsService
from squid_ui_discord.testing import InteractionHarness, MessageHarness
from tests.support.discord import make_layout_bot

BUILD_LOG_CHANNEL = 500
USER_ID = 42
AFTER_CUTOFF = Instant.from_utc(2026, 8, 5)


@pytest.fixture(autouse=True)
def _sticky_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the temporary kill switch so these tests keep covering the sticky."""
    monkeypatch.setattr("squid.bot.submission.submit.CONSENT_STICKY_ENABLED", True)


def _discord_account(*, account_id: int = 7, consented: bool) -> Account:
    return Account(
        id=account_id,
        created_at=AFTER_CUTOFF,
        identities=(AccountIdentity.discord(USER_ID),),
        consent=AccountConsent(CURRENT_CONSENT_VERSION, AFTER_CUTOFF) if consented else None,
    )


@dataclass(frozen=True)
class IdentityCreation:
    provider: IdentityProvider
    subject: str
    consent: AccountConsent | None


class AccountRecorder(AccountService):
    def __init__(self, account: Account | None) -> None:
        self.account = account
        self.reads: list[tuple[IdentityProvider, str]] = []
        self.creations: list[IdentityCreation] = []

    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        self.reads.append((provider, subject))
        return self.account

    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account:
        self.creations.append(IdentityCreation(provider, subject, consent))
        return _discord_account(consented=True)


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    async def get_locale(self, server_id: int) -> str | None:
        return None


@dataclass(frozen=True)
class Services:
    settings: SettingsService
    accounts: AccountService


@dataclass(frozen=True)
class CommunityConfig:
    build_log_channel_ids: set[int]


class StickyRecorder(BuildLogConsentStickyMessage):
    def __init__(self) -> None:
        self.triggers: list[discord.TextChannel] = []
        self.activity: list[int] = []

    async def trigger(self, channel: discord.TextChannel) -> None:
        self.triggers.append(channel)

    def record_activity(self, channel_id: int) -> None:
        self.activity.append(channel_id)


class InferenceBot:
    def __init__(self, accounts: AccountService) -> None:
        self.services = Services(settings=SettingsRecorder(), accounts=accounts)
        self.community_config = CommunityConfig(build_log_channel_ids={BUILD_LOG_CHANNEL})
        self.inference_model = "gpt-5.6-luna"
        self.inference_reasoning_effort = "low"
        self.catbox = object()

    def for_build(self, build: object) -> object:
        raise AssertionError("the stub ingestion returns no builds")


def _make_cog(*, account: Account | None) -> BuildSubmitCommands[Any]:
    cog = BuildSubmitCommands.__new__(BuildSubmitCommands)
    cog.bot = cast(Any, InferenceBot(AccountRecorder(account)))
    cog.consent_sticky = StickyRecorder()
    return cog


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


async def test_unconsented_message_triggers_sticky_and_skips_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cog = _make_cog(account=None)
    message = _make_message()
    mock_ingest = AsyncMock()
    monkeypatch.setattr("squid.bot.submission.submit.ingest_message_bundle", mock_ingest)

    await cog.infer_build_from_message(message)

    assert isinstance(cog.consent_sticky, StickyRecorder)
    assert cog.consent_sticky.triggers == [message.channel]
    assert cog.consent_sticky.activity == []
    mock_ingest.assert_not_awaited()


async def test_consented_message_records_activity_and_proceeds_with_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cog = _make_cog(account=_discord_account(consented=True))
    message = _make_message()
    mock_ingest = AsyncMock(return_value=[])
    monkeypatch.setattr("squid.bot.submission.submit.ingest_message_bundle", mock_ingest)

    await cog.infer_build_from_message(message)

    assert isinstance(cog.consent_sticky, StickyRecorder)
    assert cog.consent_sticky.triggers == []
    assert cog.consent_sticky.activity == [message.channel.id]
    mock_ingest.assert_awaited_once()


def _make_interaction(accounts: AccountService) -> Any:
    interaction = InteractionHarness(USER_ID, message_id=999)
    source = interaction.source
    source.client = make_layout_bot(
        services=Services(settings=SettingsRecorder(), accounts=accounts),
    )
    source.guild = None
    source.guild_id = None
    source.guild_locale = None
    source.locale = "en-US"
    interaction.followup.send.result = MessageHarness(message_id=999)
    return source


async def test_routed_consent_button_shows_already_consented() -> None:
    accounts = AccountRecorder(_discord_account(consented=True))
    interaction = _make_interaction(accounts)

    await open_consent_prompt(cast(Any, interaction))

    assert accounts.reads == [(IdentityProvider.DISCORD, str(USER_ID))]
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is True


async def test_routed_consent_button_grants_consent_when_user_agrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = AccountRecorder(None)
    interaction = _make_interaction(accounts)

    async def mock_wait(self: ConsentPrompt) -> AccountConsent:
        self._answer.consent = AccountConsent.grant_current()
        return self._answer.consent

    monkeypatch.setattr(ConsentPrompt, "wait", mock_wait)

    await open_consent_prompt(cast(Any, interaction))

    assert len(accounts.creations) == 1
    creation = accounts.creations[0]
    assert (creation.provider, creation.subject) == (IdentityProvider.DISCORD, str(USER_ID))
    assert creation.consent is not None
    assert creation.consent.version == CURRENT_CONSENT_VERSION
    interaction.response.send_message.assert_awaited_once()
    assert interaction.followup.send.await_count == 1


async def test_routed_consent_button_cancelling_stores_no_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = AccountRecorder(None)
    interaction = _make_interaction(accounts)

    async def mock_wait(self: ConsentPrompt) -> None:
        self._consent = None

    monkeypatch.setattr(ConsentPrompt, "wait", mock_wait)

    await open_consent_prompt(cast(Any, interaction))

    assert accounts.creations == []
    interaction.response.send_message.assert_awaited_once()
    interaction.followup.send.assert_not_awaited()
