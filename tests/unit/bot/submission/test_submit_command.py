"""`/build submit` as one request: the private defer is completed by the workspace."""

from dataclasses import dataclass
from typing import Any, cast, override

from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import CURRENT_CONSENT_VERSION, Account, AccountConsent, IdentityProvider
from squid.bot.submission.submit import BuildSubmitCommands
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.builds.application import BuildService
from squid.settings.application import SettingsService
from squid.submissions.application import FormOptionSet, StoredDraft, build_submission_manifest
from squid.submissions.domain import DraftSnapshot
from squid_ui_discord.testing import InteractionHarness
from tests.support.discord import make_layout_bot


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


@dataclass(frozen=True)
class Schematics:
    available: bool = False


class Forms:
    async def manifest(self, *, locale: str | None) -> Any:
        return build_submission_manifest()

    async def manifest_revision(self, schema_id: str, revision: int, *, locale: str | None) -> Any:
        return build_submission_manifest(revision=revision)

    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        return FormOptionSet(source, category, 1, ())


class Drafts:
    def __init__(self) -> None:
        self.current: StoredDraft | None = None

    async def create(self, **values: Any) -> StoredDraft:
        now = Instant.now()
        self.current = StoredDraft(
            snapshot=DraftSnapshot(
                values["draft_id"], values["owner_account_id"], "build_submission.v1", 1, values["category"]
            ),
            origin=values["origin"],
            created_at=now,
            updated_at=now,
            expires_at=now.add(days=7, days_assumed_24h_ok=True),
        )
        return self.current

    async def apply_change(self, draft_id: Any, actor: int, change: Any, *, locale: str | None) -> None:
        from dataclasses import replace

        assert self.current is not None
        self.current = replace(self.current, snapshot=self.current.snapshot.apply(change))

    async def get_accessible(self, draft_id: Any, actor: int) -> StoredDraft:
        assert self.current is not None
        return self.current


class Finalization:
    async def status(self, draft_id: Any, actor: int) -> None:
        return None


class Intake:
    async def list(self, draft_id: Any, actor: int) -> tuple[()]:
        return ()


@dataclass(frozen=True)
class Services:
    settings: SettingsService
    accounts: AccountService
    schematics: Schematics
    submission_forms: Forms
    submission_drafts: Drafts
    submission_finalization: Finalization
    submission_intake: Intake


def _cog() -> BuildSubmitCommands[Any]:
    cog = BuildSubmitCommands.__new__(BuildSubmitCommands)
    cog.bot = cast(
        Any,
        make_layout_bot(
            services=Services(
                SettingsRecorder(), AccountRecorder(), Schematics(), Forms(), Drafts(), Finalization(), Intake()
            )
        ),
    )
    cog.ui = cog.bot.ui.scope(cog)
    cog.builds = BuildRecorder()
    return cog


async def _submit(cog: BuildSubmitCommands[Any], **options: Any) -> InteractionHarness:
    harness = InteractionHarness(user_id=7, client=cog.bot)
    cast(Any, harness).id = 123
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
    assert isinstance(_screen(harness), DraftEditorScreen)


async def test_bad_dimensions_are_refused_in_place() -> None:
    harness = await _submit(_cog(), door_size="two by two")

    harness.followup.send.assert_not_awaited()
    harness.edit_original_response.assert_awaited_once()
    assert _screen(harness) is None
