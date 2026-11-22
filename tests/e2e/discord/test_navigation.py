"""Open screen addresses through the registered Discord command."""

from typing import Literal, override
from uuid import UUID

import pytest
import wrapture

import squid_ui.ext.discord as sd
from squid.bot.accounts.consent import ConsentPrompt
from squid.bot.accounts.workspace import AccountWorkspace
from squid.bot.catalogue.browse import BuildBrowseScreen
from squid.bot.routes import AppRequest, AppRoutes
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.core.accounts.domain import AccountConsent
from squid.core.accounts.models import Account
from squid.core.actors import Actor
from squid.core.builds.application import BuildQueryService
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build, DoorBuild
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_VIEW_PENDING
from squid.core.search.application import SearchService
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.core.submissions.application import StoredDraft, SubmissionDraftService
from squid.core.submissions.errors import DraftAccessDeniedError, DraftNotFoundError
from tests.e2e.discord.environment import command_environment, displayed_text
from tests.e2e.discord.workspace_services import WorkspaceAccounts, staff_member, staff_permissions
from tests.support.services import bot_services
from tests.support.submission_form import Schematics

NAVIGATION = "squid.bot.navigation.cog"


class BuildQueries(BuildQueryService):
    def __init__(self, status: BuildStatus | None) -> None:
        self.build = None if status is None else DoorBuild(id=12, submission_status=status)

    @override
    async def get(self, build_id: int) -> Build | None:
        assert build_id == 12
        return self.build


class SearchFields(SearchService):
    def __init__(self) -> None:
        pass

    @override
    async def fields(self) -> FieldRegistry:
        return DEFAULT_FIELD_REGISTRY


@pytest.mark.parametrize("status", [None, BuildStatus.PENDING, BuildStatus.APPROVED])
@pytest.mark.parametrize("view_pending", [False, True])
async def test_build_visibility_is_checked_before_opening(status: BuildStatus | None, view_pending: bool) -> None:
    accounts = WorkspaceAccounts()
    permissions = staff_permissions(*((BUILD_SUBMISSION_VIEW_PENDING,) if view_pending else ()))
    services = bot_services(
        accounts=accounts,
        permissions=permissions,
        build_queries=BuildQueries(status),
        search=SearchFields(),
        schematics=Schematics(),
    )
    async with command_environment(NAVIGATION, services=services) as (bot, env):
        bot.ui_router.mount("builds", BuildBrowseScreen.open_detail)
        channel = env.guild.create_text_channel("builds")
        member = staff_member(env, accounts)

        result = await member.slash(channel, "open", address="/builds/12")

        assert result.response is not None
        assert result.response.ephemeral
        text = "\n".join(displayed_text(result.response.message))
        if status is BuildStatus.APPROVED or (status is BuildStatus.PENDING and view_pending):
            assert "Submission ID: 12" in text
        else:
            assert "That build is unavailable." in text
            assert "Submission ID: 12" not in text


class UnavailableDrafts(SubmissionDraftService):
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.lookups: list[tuple[UUID, int | None]] = []

    @override
    async def get_accessible(self, draft_id: UUID, actor: Actor) -> StoredDraft:
        self.lookups.append((draft_id, actor.account_id))
        raise self.error


async def test_declining_consent_does_not_look_up_a_draft() -> None:
    drafts = UnavailableDrafts(DraftNotFoundError(UUID(int=1)))
    services = bot_services(accounts=WorkspaceAccounts(), submission_drafts=drafts)
    async with command_environment(NAVIGATION, services=services) as (bot, env):
        bot.ui_router.mount("drafts", DraftEditorScreen.open_draft)
        wait = wrapture.binding(ConsentPrompt, "wait").on_call.decorates(
            lambda wrapped, _instance, args, kwargs: env.external_wait(
                wrapped(*args, **kwargs), reason="member consent"
            )
        )
        channel = env.guild.create_text_channel("builds")
        member = env.guild.add_member(env.create_user("builder"))

        with wait:
            opened = await member.slash(channel, "open", address=f"/drafts/{UUID(int=1)}")
            assert opened.response is not None
            assert opened.response.ephemeral
            assert not drafts.lookups
            decision = await member.click(opened.response.message, label="Cancel")

        assert decision.acknowledged
        assert not drafts.lookups
        assert any(
            "Cancelled. No account information was stored." in displayed_text(message)
            for message in channel.history(viewer=member)
        )


@pytest.mark.parametrize("error", [DraftNotFoundError(UUID(int=1)), DraftAccessDeniedError()])
async def test_draft_access_failure_does_not_open_editor(error: Exception) -> None:
    drafts, accounts = UnavailableDrafts(error), WorkspaceAccounts()
    services = bot_services(accounts=accounts, submission_drafts=drafts)
    async with command_environment(NAVIGATION, services=services) as (bot, env):
        bot.ui_router.mount("drafts", DraftEditorScreen.open_draft)
        channel = env.guild.create_text_channel("builds")
        member = env.guild.add_member(env.create_user("builder"))
        accounts.discord_accounts[str(member.id)] = Account(id=7, consent=AccountConsent.grant_current())
        draft_id = UUID(int=1)

        result = await member.slash(channel, "open", address=f"/drafts/{draft_id}")

        assert result.response is not None
        assert result.response.ephemeral
        assert "That draft is unavailable." in displayed_text(result.response.message)
        assert drafts.lookups == [(draft_id, 7)]


@pytest.mark.parametrize(
    ("tab", "expected"),
    [
        ("review", "That account tab is unavailable."),
        ("accounts", "Connect Minecraft"),
        ("notifications", "Review the privacy notice to access your inbox"),
    ],
)
async def test_account_address_respects_available_tabs(tab: str, expected: str) -> None:
    services = bot_services(accounts=WorkspaceAccounts(), permissions=staff_permissions())
    async with command_environment(NAVIGATION, services=services) as (bot, env):
        bot.ui_router.mount("accounts", AccountWorkspace.open_account)
        channel = env.guild.create_text_channel("community")
        member = env.guild.add_member(env.create_user("builder"))

        result = await member.slash(channel, "open", address=f"/account#{tab}")

        assert result.response is not None
        assert result.response.ephemeral
        assert any(expected in text for text in displayed_text(result.response.message))


@pytest.mark.parametrize(
    "address", ["builds/12", "/builds/12", "  builds/12  ", "drafts?inbox=true", "account#credits"]
)
async def test_navigation_accepts_optional_leading_slash(address: str) -> None:
    routes = AppRoutes()

    @routes.route("/builds/{build_id}")
    async def build(request: AppRequest, build_id: int) -> sd.Response:
        return sd.Response(f"Build {build_id} for {request.user.id}")

    @routes.route("/drafts")
    async def drafts(request: AppRequest, inbox: bool = False) -> sd.Response:
        return sd.Response(f"Draft inbox: {inbox} for {request.user.id}")

    @routes.route("/account", fragment="tab")
    async def account(request: AppRequest, tab: Literal["credits"] | None = None) -> sd.Response:
        return sd.Response(f"Account tab: {tab} for {request.user.id}")

    async with command_environment(NAVIGATION) as (bot, env):
        bot.ui_router.mount("test", build, drafts, account)
        channel = env.guild.create_text_channel("community")
        member = env.guild.add_member(env.create_user("builder"))

        result = await member.slash(channel, "open", address=address)

        expected = {
            "builds/12": "Build 12",
            "drafts?inbox=true": "Draft inbox: True",
            "account#credits": "Account tab: credits",
        }[address.strip().lstrip("/")]
        assert result.response is not None
        assert result.response.ephemeral
        assert result.response.message.content == f"{expected} for {member.id}"


@pytest.mark.parametrize("address", ["", "  ", "https://example.com/builds", "//example.com/builds", "?inbox=true"])
async def test_navigation_rejects_non_screen_addresses(address: str) -> None:
    async with command_environment(NAVIGATION) as (_, env):
        channel = env.guild.create_text_channel("community")
        member = env.guild.add_member(env.create_user("builder"))

        result = await member.slash(channel, "open", address=address)

        assert result.response is not None
        assert result.response.ephemeral
        assert any("That screen address is invalid." in text for text in displayed_text(result.response.message))
