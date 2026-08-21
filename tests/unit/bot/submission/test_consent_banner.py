"""Unit tests for the build log consent banner and its routed button."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, Account, AccountConsent, AccountIdentity, IdentityProvider
from squid.bot.consent import ConsentPrompt
from squid.bot.submission.consent_banner import (
    BuildLogConsentStickyMessage,
    open_consent_prompt,
)
from squid.bot.submission.submit import BuildSubmitCommands
from squid.bot.utils.mount_registry import MountRegistry
from squid_layouts.discord.testing import fake_message

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


def _make_cog(*, account: Account | None) -> BuildSubmitCommands[Any]:
    cog = BuildSubmitCommands.__new__(BuildSubmitCommands)
    accounts = AsyncMock()
    accounts.get_account_by_identity.return_value = account
    accounts.get_or_create_identity.return_value = _discord_account(consented=True)

    bot = SimpleNamespace(
        services=SimpleNamespace(
            settings=SimpleNamespace(),
            accounts=accounts,
        ),
        community_config=SimpleNamespace(build_log_channel_ids={BUILD_LOG_CHANNEL}),
        inference_model="gpt-5.6-luna",
        inference_reasoning_effort="low",
        catbox=SimpleNamespace(),
        for_build=MagicMock(return_value=SimpleNamespace(post_for_voting=AsyncMock())),
    )
    cog.bot = cast(Any, bot)
    cog.consent_sticky = MagicMock(spec=BuildLogConsentStickyMessage)
    cog.consent_sticky.trigger = AsyncMock()
    cog.consent_sticky.record_activity = MagicMock()
    return cog


def _make_message(*, author_id: int = USER_ID, channel_id: int = BUILD_LOG_CHANNEL) -> discord.Message:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.history = MagicMock(return_value=_empty_history())

    author = SimpleNamespace(id=author_id, bot=False)
    return cast(
        discord.Message,
        cast(Any, SimpleNamespace(id=100, author=author, channel=channel, attachments=[])),
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

    cast(Any, cog.consent_sticky).trigger.assert_awaited_once_with(message.channel)
    cast(Any, cog.consent_sticky).record_activity.assert_not_called()
    mock_ingest.assert_not_awaited()


async def test_consented_message_records_activity_and_proceeds_with_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cog = _make_cog(account=_discord_account(consented=True))
    message = _make_message()
    mock_ingest = AsyncMock(return_value=[])
    monkeypatch.setattr("squid.bot.submission.submit.ingest_message_bundle", mock_ingest)

    await cog.infer_build_from_message(message)

    cast(Any, cog.consent_sticky).trigger.assert_not_awaited()
    cast(Any, cog.consent_sticky).record_activity.assert_called_once_with(message.channel.id)
    mock_ingest.assert_awaited_once()


def _make_interaction(accounts: Any) -> Any:
    return SimpleNamespace(
        user=SimpleNamespace(id=USER_ID),
        guild=None,
        guild_id=None,
        guild_locale=None,
        locale="en-US",
        client=SimpleNamespace(
            services=SimpleNamespace(
                settings=SimpleNamespace(),
                accounts=accounts,
            ),
            mounts=MountRegistry(),
        ),
        response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock(return_value=fake_message(message_id=999))),
    )


async def test_routed_consent_button_shows_already_consented() -> None:
    accounts = AsyncMock()
    accounts.get_account_by_identity.return_value = _discord_account(consented=True)
    interaction = _make_interaction(accounts)

    await open_consent_prompt(cast(Any, interaction))

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    accounts.get_account_by_identity.assert_awaited_once_with(IdentityProvider.DISCORD, str(USER_ID))
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs.get("ephemeral") is True


async def test_routed_consent_button_grants_consent_when_user_agrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = AsyncMock()
    accounts.get_account_by_identity.return_value = None
    accounts.get_or_create_identity.return_value = _discord_account(consented=True)
    interaction = _make_interaction(accounts)

    async def mock_wait(self: ConsentPrompt) -> None:
        self._consent = AccountConsent.grant_current()

    monkeypatch.setattr(ConsentPrompt, "wait", mock_wait)

    await open_consent_prompt(cast(Any, interaction))

    accounts.get_or_create_identity.assert_awaited_once()
    call = accounts.get_or_create_identity.await_args
    assert call.args == (IdentityProvider.DISCORD, str(USER_ID))
    assert call.kwargs["consent"].version == CURRENT_CONSENT_VERSION
    assert interaction.followup.send.await_count == 2


async def test_routed_consent_button_cancelling_stores_no_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = AsyncMock()
    accounts.get_account_by_identity.return_value = None
    interaction = _make_interaction(accounts)

    async def mock_wait(self: ConsentPrompt) -> None:
        self._consent = None

    monkeypatch.setattr(ConsentPrompt, "wait", mock_wait)

    await open_consent_prompt(cast(Any, interaction))

    accounts.get_or_create_identity.assert_not_awaited()
    assert interaction.followup.send.await_count == 1
