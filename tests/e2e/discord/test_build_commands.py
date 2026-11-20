"""Submit and reopen drafts, search the catalogue, and review build messages through Discord."""

from typing import override
from uuid import UUID

import pytest

from squid.core.accounts.domain import AccountConsent
from squid.core.accounts.models import Account
from squid.core.actors import Actor
from squid.core.builds.application import BuildQueryService, BuildService
from squid.core.builds.domain import Status
from squid.core.builds.models import Build, DoorBuild
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT, BUILD_SUBMISSION_RECALC
from squid.core.search.application import SearchService
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.core.search.domain import MetadataSearchHit, RecordSearchHit, SearchPage, SearchRequest, SearchScope
from squid.core.submissions.application.inference_runs import SubmissionInferenceRuns
from tests.e2e.discord.environment import command_environment, displayed_text
from tests.e2e.discord.workspace_services import WorkspaceAccounts, staff_member, staff_permissions
from tests.support.services import bot_services
from tests.support.settings import RuntimeSettingsStub
from tests.support.submission_form import Drafts, Finalization, Forms, Intake, Schematics

EXTENSION = "squid.bot.submission.cog"


class EmptyInference(SubmissionInferenceRuns):
    def __init__(self) -> None:
        pass

    @override
    async def list_active(self, actor: Actor, *, inbox: bool = False) -> tuple[UUID, ...]:
        return ()


async def test_submit_a_named_door_and_find_it_in_private_drafts() -> None:
    drafts = Drafts()
    accounts = WorkspaceAccounts()
    services = bot_services(
        accounts=accounts,
        schematics=Schematics(),
        submission_forms=Forms(),
        submission_drafts=drafts,
        submission_finalization=Finalization(),
        submission_intake=Intake(),
        submission_inference=EmptyInference(),
    )
    async with command_environment(EXTENSION, services=services) as (_, env):
        channel = env.guild.create_text_channel("submissions")
        author = env.guild.add_member(env.create_user("author"))
        other = env.guild.add_member(env.create_user("other"))
        accounts.discord_accounts[str(author.id)] = Account(id=7, consent=AccountConsent.grant_current())
        accounts.discord_accounts[str(other.id)] = Account(id=9, consent=AccountConsent.grant_current())

        submitted = await author.slash(channel, "build submit", name="Aurora", door_size="2x2")
        assert submitted.response is not None
        assert submitted.response.ephemeral
        assert any("Submit a build" in line for line in displayed_text(submitted.response.message))
        assert drafts.current is not None
        assert drafts.current.snapshot.owner_account_id == 7
        assert drafts.current.snapshot.answers["opening_width"] == 2
        assert drafts.current.snapshot.answers["opening_height"] == 2

        listed = await author.slash(channel, "build drafts")
        assert listed.response is not None
        assert listed.response.ephemeral
        assert any("Aurora" in line and "In progress" in line for line in displayed_text(listed.response.message))

        reopened = await author.click(listed.response.message, label="Open")
        assert reopened.response is not None
        assert reopened.response.ephemeral
        assert any("Submit a build" in line for line in displayed_text(reopened.response.message))

        other_drafts = await other.slash(channel, "build drafts")
        assert other_drafts.response is not None
        assert other_drafts.response.ephemeral
        assert any("You have no drafts in progress" in line for line in displayed_text(other_drafts.response.message))


class EmptyAttachmentQueue(BuildService):
    def __init__(self) -> None:
        pass

    @override
    async def next_attachment_review(self, after: int) -> int | None:
        return None


async def test_attachment_reviewer_reaches_end_of_queue_privately() -> None:
    accounts = WorkspaceAccounts()
    services = bot_services(
        accounts=accounts, permissions=staff_permissions(BUILD_SUBMISSION_EDIT), builds=EmptyAttachmentQueue()
    )
    async with command_environment(EXTENSION, services=services) as (_, env):
        channel = env.guild.create_text_channel("moderation")
        reviewer = staff_member(env, accounts)

        result = await reviewer.slash(channel, "build issues")

        assert result.response is not None
        assert result.response.ephemeral
        assert any("End of the attachment queue" in line for line in displayed_text(result.response.message))


class CatalogueQueries(BuildQueryService):
    def __init__(self) -> None:
        self.pending_build = DoorBuild(id=73, submission_status=Status.PENDING)

    @override
    async def get(self, build_id: int) -> Build | None:
        assert build_id == self.pending_build.id
        return self.pending_build


class CatalogueSearch(SearchService):
    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

    @override
    async def fields(self) -> FieldRegistry:
        return DEFAULT_FIELD_REGISTRY

    @override
    async def search(self, request: SearchRequest) -> SearchPage:
        self.requests.append(request)
        if request.scope is SearchScope.RECORDS:
            hit = RecordSearchHit("7", "Smallest spiral door", None, 5, "Aurora", "smallest", "all_time")
        else:
            assert request.scope is SearchScope.METADATA
            hit = MetadataSearchHit("9", "Spiral piston layout", "pattern")
        return SearchPage((hit,), total=1, next=None, prev=None)


@pytest.mark.parametrize(
    ("command", "options", "scope", "query", "title"),
    [
        (
            "search",
            {"scope": "patterns", "query": "spiral"},
            SearchScope.METADATA,
            "kind:pattern (spiral)",
            "Spiral piston layout",
        ),
        ("records", {"query": "spiral"}, SearchScope.RECORDS, "spiral", "Smallest spiral door"),
    ],
)
async def test_catalogue_search_uses_the_selected_scope(
    command: str, options: dict[str, str], scope: SearchScope, query: str, title: str
) -> None:
    search = CatalogueSearch()
    services = bot_services(
        accounts=WorkspaceAccounts(), permissions=staff_permissions(), build_queries=CatalogueQueries(), search=search
    )
    async with command_environment(EXTENSION, services=services) as (_, env):
        channel = env.guild.create_text_channel("catalogue")
        reader = env.guild.add_member(env.create_user("reader"))

        result = await reader.slash(channel, command, **options)

        assert result.response is not None
        assert not result.response.ephemeral
        assert any(title in line for line in displayed_text(result.response.message))
        assert [(r.scope, r.query) for r in search.requests] == [(scope, query)]


async def test_browse_by_id_hides_an_unpublished_build_from_a_reader() -> None:
    services = bot_services(
        accounts=WorkspaceAccounts(),
        permissions=staff_permissions(),
        search=CatalogueSearch(),
        build_queries=CatalogueQueries(),
    )
    async with command_environment(EXTENSION, services=services) as (_, env):
        channel = env.guild.create_text_channel("catalogue")
        reader = env.guild.add_member(env.create_user("reader"))

        result = await reader.slash(channel, "build browse", id=73)

        assert result.response is not None
        assert any("That build is unavailable" in line for line in displayed_text(result.response.message))


class BuildLogSettings(RuntimeSettingsStub):
    @override
    async def get_locale(self, server_id: int) -> str | None:
        return None


async def test_scanning_a_message_outside_the_build_log_explains_the_channel_requirement() -> None:
    accounts = WorkspaceAccounts()
    services = bot_services(
        accounts=accounts, settings=BuildLogSettings(), permissions=staff_permissions(BUILD_SUBMISSION_RECALC)
    )
    async with command_environment(EXTENSION, services=services) as (_, env):
        channel = env.guild.create_text_channel("general")
        author = env.guild.add_member(env.create_user("author"))
        reviewer = staff_member(env, accounts)
        message = await author.send(channel, "My new spiral door")

        result = await reviewer.context_menu(channel, "Scan Build Message", message)

        assert result.response is not None
        assert result.response.ephemeral
        assert any("posted in a build log channel" in line for line in displayed_text(result.response.message))
