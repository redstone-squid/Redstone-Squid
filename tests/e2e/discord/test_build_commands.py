"""Submit and reopen drafts, search the catalogue, and review build messages through Discord."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import override
from uuid import UUID

import discord
import pytest
import simcord

from squid.core.accounts.domain import PrivacyNoticeReceipt
from squid.core.actors import ActingContext
from squid.core.builds.application import BuildQueryService, BuildService
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build, DoorBuild
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT, BUILD_SUBMISSION_RECALC
from squid.core.search.application import SearchService
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.core.search.application.results import BuildSearchHit, MetadataSearchHit, RecordSearchHit, SearchPage
from squid.core.search.domain import SearchRequest, SearchScope
from squid.core.submissions.application.inference_runs import SubmissionInferenceRuns
from tests.e2e.discord.environment import command_environment, displayed_text
from tests.e2e.discord.workspace_services import staff_member, staff_permissions
from tests.support.accounts import FakeAccountRepository, InMemoryAccountService
from tests.support.services import bot_services
from tests.support.settings import RuntimeSettingsStub
from tests.support.submission_form import Drafts, Finalization, Forms, Intake, Schematics

EXTENSION = "squid.bot.submission.cog"


class EmptyInference(SubmissionInferenceRuns):
    def __init__(self) -> None:
        pass

    @override
    async def list_active(self, *, context: ActingContext, inbox: bool = False) -> tuple[UUID, ...]:
        return ()


@asynccontextmanager
async def submission() -> AsyncIterator[tuple[simcord.Env, simcord.MemberActor, Drafts, FakeAccountRepository]]:
    drafts = Drafts()
    accounts = FakeAccountRepository()
    services = bot_services(
        accounts=InMemoryAccountService(accounts),
        schematics=Schematics(),
        submission_forms=Forms(),
        submission_drafts=drafts,
        submission_finalization=Finalization(),
        submission_intake=Intake(),
        submission_inference=EmptyInference(),
    )
    async with command_environment(EXTENSION, services=services) as (_, env):
        author = env.guild.add_member(env.create_user("author"))
        accounts.seed_account(author.id, account_id=7, consent=PrivacyNoticeReceipt.grant_current())
        yield env, author, drafts, accounts


async def test_submit_a_named_door_and_find_it_in_private_drafts() -> None:
    async with submission() as (env, author, drafts, accounts):
        channel = env.guild.create_text_channel("submissions")
        other = env.guild.add_member(env.create_user("other"))
        # The other member has a consented account but owns no drafts.
        accounts.seed_account(other.id, account_id=9, consent=PrivacyNoticeReceipt.grant_current())

        submitted = await author.slash(channel, "build submit", name="Aurora", opening_size="2x2")
        assert submitted.response is not None
        assert submitted.response.ephemeral
        assert not submitted.followups
        (callback,) = (call for call in env.http_requests if call.method == "POST" and call.path.endswith("/callback"))
        assert callback.json is not None
        assert callback.json["type"] == discord.InteractionResponseType.deferred_channel_message.value
        assert any(call.method == "PATCH" and call.path.endswith("/messages/@original") for call in env.http_requests)
        assert any("Build draft" in line for line in displayed_text(submitted.response.message))
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
        assert any("Build draft" in line for line in displayed_text(reopened.response.message))

        other_drafts = await other.slash(channel, "build drafts")
        assert other_drafts.response is not None
        assert other_drafts.response.ephemeral
        assert any("You have no drafts in progress" in line for line in displayed_text(other_drafts.response.message))


@pytest.mark.parametrize("category", ["Door", "Extender", "Utility", "Entrance", "Other"])
async def test_submit_prefills_shared_fields_for_each_category(category: str) -> None:
    async with submission() as (env, author, drafts, _):
        channel = env.guild.create_text_channel("submissions")
        result = await author.slash(
            channel, "build submit", kind=category, name="My build", description="Details", build_dimensions="5x7x4"
        )

        assert result.response is not None
        assert result.response.ephemeral
        assert drafts.current is not None
        snapshot = drafts.current.snapshot
        assert snapshot.category == category.lower()
        assert snapshot.answers["display_name"] == "My build"
        assert snapshot.answers["description"] == "Details"
        assert (
            snapshot.answers["capture_width"],
            snapshot.answers["capture_height"],
            snapshot.answers["capture_depth"],
        ) == (5, 7, 4)
        assert "opening_width" not in snapshot.answers


async def test_submit_names_values_the_form_cannot_use() -> None:
    async with submission() as (env, author, drafts, _):
        channel = env.guild.create_text_channel("submissions")
        result = await author.slash(channel, "build submit", opening_size="5 wide", build_dimensions="5x7x4")

        assert result.response is not None
        assert result.response.ephemeral
        assert any(
            "Some values from the command could not be used: Opening size." in line
            for line in displayed_text(result.response.message)
        )
        assert drafts.current is not None
        assert drafts.current.snapshot.answers["opening_width"] == 5
        assert "opening_height" not in drafts.current.snapshot.answers


@pytest.mark.parametrize(
    ("options", "detail"),
    [
        ({"opening_size": "two by two"}, "Check the opening size"),
        ({"kind": "Extender", "opening_size": "2x2"}, "Opening size and orientation can only be supplied for doors."),
        ({"kind": "Utility", "patterns": "Regular"}, "Patterns can only be supplied for doors and extenders."),
    ],
)
async def test_submit_rejects_invalid_prefills_before_creating_a_draft(
    options: dict[str, str],
    detail: str,
) -> None:
    async with submission() as (env, author, drafts, _):
        channel = env.guild.create_text_channel("submissions")
        result = await author.slash(channel, "build submit", **options)

        assert result.response is not None
        assert result.response.ephemeral
        assert not result.followups
        assert any(detail in line for line in displayed_text(result.response.message))
        assert drafts.current is None


class EmptyAttachmentQueue(BuildService):
    def __init__(self) -> None:
        pass

    @override
    async def next_attachment_review(self, after: int, *, context: ActingContext) -> int | None:
        return None


async def test_attachment_reviewer_reaches_end_of_queue_privately() -> None:
    accounts = FakeAccountRepository()
    services = bot_services(
        accounts=InMemoryAccountService(accounts),
        permissions=staff_permissions(BUILD_SUBMISSION_EDIT),
        builds=EmptyAttachmentQueue(),
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
        self.pending_build = DoorBuild(id=73, submission_status=BuildStatus.PENDING)

    @override
    async def get(self, build_id: int, *, context: ActingContext) -> Build | None:
        assert build_id == self.pending_build.id
        return self.pending_build


class CatalogueSearch(SearchService):
    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

    @override
    async def fields(self, *, context: ActingContext) -> FieldRegistry:
        return DEFAULT_FIELD_REGISTRY

    @override
    async def search(self, request: SearchRequest, *, context: ActingContext) -> SearchPage:
        self.requests.append(request)
        if request.scope is SearchScope.RECORDS:
            hit = RecordSearchHit("7", "Smallest spiral door", None, 5, "Aurora", "smallest", "all_time")
        elif request.scope is SearchScope.METADATA:
            hit = MetadataSearchHit("9", "Spiral piston layout", "pattern")
        else:
            assert request.scope is SearchScope.BUILDS
            hit = BuildSearchHit("73", "Five-wide door", "approved")
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
        accounts=InMemoryAccountService(),
        permissions=staff_permissions(),
        build_queries=CatalogueQueries(),
        search=search,
    )
    async with command_environment(EXTENSION, services=services) as (_, env):
        channel = env.guild.create_text_channel("catalogue")
        reader = env.guild.add_member(env.create_user("reader"))

        result = await reader.slash(channel, command, **options)

        assert result.response is not None
        assert not result.response.ephemeral
        assert any(title in line for line in displayed_text(result.response.message))
        assert [(r.scope, r.query) for r in search.requests] == [(scope, query)]


@pytest.mark.parametrize("mention", ["<@{bot_id}>", "<@!{bot_id}>"])
async def test_mention_searches_builds(mention: str) -> None:
    search = CatalogueSearch()
    services = bot_services(
        accounts=InMemoryAccountService(),
        permissions=staff_permissions(),
        build_queries=CatalogueQueries(),
        search=search,
        settings=BuildLogSettings(),
    )
    async with command_environment(EXTENSION, services=services) as (bot, env):
        assert bot.user is not None
        channel = env.guild.create_text_channel("catalogue")
        reader = env.guild.add_member(env.create_user("reader"))

        await reader.send(channel, f"{mention.format(bot_id=bot.user.id)} width:5  ")

        assert [(request.scope, request.query) for request in search.requests] == [(SearchScope.BUILDS, "width:5")]
        result = channel.last_message
        assert result is not None
        assert not result.flags.ephemeral
        assert any("Five-wide door" in line for line in displayed_text(result))


async def test_browse_by_id_hides_an_unpublished_build_from_a_reader() -> None:
    services = bot_services(
        accounts=InMemoryAccountService(),
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
    async def get_locale(self, server_id: int, *, context: ActingContext) -> str | None:
        return None


async def test_scanning_a_message_outside_the_build_log_explains_the_channel_requirement() -> None:
    accounts = FakeAccountRepository()
    services = bot_services(
        accounts=InMemoryAccountService(accounts),
        settings=BuildLogSettings(),
        permissions=staff_permissions(BUILD_SUBMISSION_RECALC),
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
