"""`/build submit` as one request: the private defer is completed by the workspace."""

from dataclasses import dataclass
from typing import Any, cast

from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import CURRENT_CONSENT_VERSION, Account, AccountConsent, IdentityProvider
from squid.bot.submission.submit import BuildSubmitCommands
from squid.bot.submission.ui.views import SubmissionScreen
from squid.builds.application import BuildService
from squid.settings.application import SettingsService
from squid_ui_discord.testing import InteractionHarness
from tests.support.discord import make_layout_bot


class AccountRecorder(AccountService):
    def __init__(self) -> None:
        self.account = Account(
            id=1,
            created_at=Instant.from_utc(2026, 8, 29),
            consent=AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 29)),
        )

    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        return self.account


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    async def get_locale(self, server_id: int) -> str | None:
        return None


class BuildRecorder(BuildService):
    def __init__(self) -> None:
        pass


@dataclass(frozen=True)
class Schematics:
    available: bool = False


@dataclass(frozen=True)
class Services:
    settings: SettingsService
    accounts: AccountService
    schematics: Schematics


def _cog() -> BuildSubmitCommands[Any]:
    cog = BuildSubmitCommands.__new__(BuildSubmitCommands)
    cog.bot = cast(
        Any,
        make_layout_bot(services=Services(SettingsRecorder(), AccountRecorder(), Schematics())),
    )
    cog.ui = cog.bot.ui.scope(cog)
    cog.builds = BuildRecorder()
    return cog


async def _submit(cog: BuildSubmitCommands[Any], **options: Any) -> InteractionHarness:
    harness = InteractionHarness(user_id=7, client=cog.bot)
    harness.guild = None
    await cast(Any, BuildSubmitCommands.submit_form).callback(cog, harness.source, **options)
    return harness


def _screen(harness: InteractionHarness) -> Any:
    call = harness.edit_original_response.await_args
    assert call is not None
    return getattr(getattr(call.kwargs["view"], "_root", None), "component", None)


async def test_the_workspace_completes_the_private_defer() -> None:
    """Before the request ledger, the workspace resolved a second request that did not know
    about the defer and went out as a follow-up, leaving the "thinking" placeholder forever."""
    harness = await _submit(_cog(), door_size="2x2")

    harness.response.defer.assert_awaited_once()
    assert harness.response.defer.await_args is not None
    assert harness.response.defer.await_args.kwargs["ephemeral"] is True
    harness.followup.send.assert_not_awaited()
    assert isinstance(_screen(harness), SubmissionScreen)


async def test_bad_dimensions_are_refused_in_place() -> None:
    harness = await _submit(_cog(), door_size="two by two")

    harness.followup.send.assert_not_awaited()
    harness.edit_original_response.assert_awaited_once()
    assert _screen(harness) is None
