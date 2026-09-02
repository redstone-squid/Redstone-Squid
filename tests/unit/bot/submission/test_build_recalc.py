"""The "Recalculate Build" context menu: who may run it, and on what."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import MagicMock

import discord
import pytest
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import CURRENT_CONSENT_VERSION, Account, AccountConsent, IdentityProvider
from squid.bot.submission.consent_banner import BuildLogConsentStickyMessage
from squid.bot.submission.submit import BuildSubmitCommands
from squid.bot.utils.permissions import PermissionNodeRequired
from squid.permissions.application import PermissionService
from squid.permissions.domain import Decision, PermissionNode, Reason, Subject
from squid.settings.application import SettingsService
from squid_ui_discord.testing import InteractionHarness, invoke_context_menu
from tests.support.discord import make_layout_bot

BUILD_LOG_CHANNEL = 500


@pytest.fixture(autouse=True)
def _sticky_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the temporary kill switch so these tests keep covering the sticky."""
    monkeypatch.setattr("squid.bot.submission.submit.CONSENT_STICKY_ENABLED", True)


class StubPermissions(PermissionService):
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed

    async def decisions(self, subject: Subject, nodes: Iterable[PermissionNode | str]) -> tuple[Decision, ...]:
        return tuple(
            Decision(
                node=node.name if isinstance(node, PermissionNode) else node,
                allowed=self.allowed,
                reason=Reason.DEFAULT,
            )
            for node in nodes
        )


class AccountRecorder(AccountService):
    def __init__(self, account: Account | None) -> None:
        self.account = account

    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        return self.account


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    async def get_locale(self, server_id: int) -> str | None:
        return None


class AccountIdResolver:
    async def resolve(self, accounts: AccountService, discord_id: int) -> int | None:
        return 1


class OwnerCheck:
    async def __call__(self, user: object) -> bool:
        return False


@dataclass(frozen=True)
class CommunityConfig:
    build_log_channel_ids: set[int]


class ConsentStickyRecorder(BuildLogConsentStickyMessage):
    def __init__(self) -> None:
        self.calls: list[discord.TextChannel] = []

    async def trigger(self, channel: discord.TextChannel) -> None:
        self.calls.append(channel)


class RecordingSubmitCommands(BuildSubmitCommands[Any]):
    def __init__(self) -> None:
        self.inferred: list[discord.Message] = []

    async def infer_build_from_message(self, message: discord.Message) -> None:
        self.inferred.append(message)


@dataclass(frozen=True)
class Services:
    settings: SettingsService
    accounts: AccountService
    permissions: PermissionService


def _cog(*, allowed: bool = True, account_consented: bool = True) -> RecordingSubmitCommands:
    cog = RecordingSubmitCommands()
    account = (
        Account(
            id=1,
            created_at=Instant.from_utc(2026, 8, 29),
            consent=AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 29)),
        )
        if account_consented
        else None
    )

    cog.bot = cast(
        Any,
        make_layout_bot(
            services=Services(
                settings=SettingsRecorder(),
                accounts=AccountRecorder(account),
                permissions=StubPermissions(allowed=allowed),
            ),
            account_ids=AccountIdResolver(),
            is_owner=OwnerCheck(),
            community_config=CommunityConfig(build_log_channel_ids={BUILD_LOG_CHANNEL}),
        ),
    )
    cog.ui = cog.bot.ui.scope(cog)
    cog.consent_sticky = ConsentStickyRecorder()
    return cog


def _interaction() -> discord.Interaction[Any]:
    interaction = InteractionHarness(user_id=7).source
    interaction.guild = None
    interaction.guild_id = None
    interaction.guild_locale = None
    interaction.locale = "en-US"
    interaction.client = None
    return cast(discord.Interaction[Any], interaction)


def _message(*, channel_id: int = BUILD_LOG_CHANNEL, from_bot: bool = False) -> discord.Message:
    # A real TextChannel, because the eligibility check is an isinstance test: inference reads
    # the channel's history, which a thread or a DM does not offer the same way.
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

    return cast(discord.Message, Message(author=Author(id=42, bot=from_bot), channel=channel))


async def _run(cog: BuildSubmitCommands[Any], message: discord.Message) -> discord.Interaction[Any]:
    interaction = _interaction()
    cast(Any, interaction).client = cog.bot
    await invoke_context_menu(cog, cog.recalc_context_menu, interaction, message)
    return interaction


async def test_the_menu_denies_the_way_the_command_did() -> None:
    """A context menu cannot carry `requires(...)`, so it raises what the decorator raises
    and the shared presenter renders one refusal for both surfaces."""
    cog = _cog(allowed=False)

    with pytest.raises(PermissionNodeRequired) as denial:
        await _run(cog, _message())

    assert denial.value.nodes == ("build.submission.recalc",)
    assert cog.inferred == []


async def test_a_message_no_build_can_come_from_says_so() -> None:
    """The command reported "Build recalculated." whatever it was pointed at, because
    inference is a listener that silently ignores anything outside a build log channel."""
    cog = _cog()

    interaction = await _run(cog, _message(channel_id=999))

    assert cog.inferred == []
    assert cast(Any, interaction).edit_original_response.await_count == 1


async def test_a_build_log_message_is_recalculated() -> None:
    cog = _cog()
    message = _message()

    await _run(cog, message)

    assert cog.inferred == [message]


async def test_recalc_refuses_when_author_is_unconsented() -> None:
    cog = _cog(account_consented=False)
    message = _message()

    interaction = await _run(cog, message)

    assert cog.inferred == []
    assert cast(Any, interaction).edit_original_response.await_count == 1
    assert isinstance(cog.consent_sticky, ConsentStickyRecorder)
    assert cog.consent_sticky.calls == [message.channel]
