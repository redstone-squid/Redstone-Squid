"""Build fresh destinations using the same services as their original commands."""

from functools import partial
from typing import Any

import squid_ui.ext.discord as sd
from squid.bot.accounts.cog import make_account_workspace
from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.navigation.destinations import AccountDestination, BuildDetail, BuildIndex, DraftDetail, DraftIndex
from squid.bot.navigation.router import NavigationRequest, ScreenOpening
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.commands import make_catalogue
from squid.bot.submission.submit import make_saved_drafts
from squid.bot.submission.ui.drafts import draft_editor
from squid.bot.ui import text_node
from squid.builds.domain import Status
from squid.core.i18n import tr
from squid.submissions.errors import DraftAccessDeniedError, DraftNotFoundError


async def make_build_index(request: NavigationRequest, destination: BuildIndex) -> ScreenOpening:
    return ScreenOpening(sd.Response[Any](await make_catalogue(request), audience="personal"))


async def make_build_detail(request: NavigationRequest, destination: BuildDetail) -> ScreenOpening:
    screen = await make_catalogue(request, build_id=destination.build_id)
    build = await request.client.services.build_queries.get(destination.build_id)
    if build is None or (build.submission_status is not Status.CONFIRMED and not screen._capabilities.view_pending):
        return ScreenOpening(
            sd.Response[Any](
                text_node(tr(t"navigation-build-unavailable = That build is unavailable.")), audience="personal"
            )
        )
    return ScreenOpening(sd.Response[Any](screen, audience="personal"))


async def make_draft_index(request: NavigationRequest, destination: DraftIndex) -> ScreenOpening | None:
    content = await make_saved_drafts(request, inbox=destination.inbox)
    return None if content is None else ScreenOpening(sd.Response[Any](content, audience="personal"))


async def make_draft_detail(request: NavigationRequest, destination: DraftDetail) -> ScreenOpening | None:
    services = request.client.services
    if await ensure_consented_account(request, services.accounts) is None:
        return None
    draft_id = destination.draft_id
    try:
        screen = await draft_editor(
            services,
            draft_id,
            changes=request.client.topic_bus.topic("submission_draft", str(draft_id)),
            resolve_actor=partial(request.client.actors.resolve, request, refresh=True),
            render_card=lambda build: render_build_card(
                build, request.client.media_previews, schematic_summaries=services.schematic_summaries
            ),
        )
    except DraftNotFoundError, DraftAccessDeniedError:
        return ScreenOpening(
            sd.Response[Any](
                text_node(tr(t"navigation-draft-unavailable = That draft is unavailable.")), audience="personal"
            )
        )
    return ScreenOpening(
        sd.Response[Any](screen, audience="personal"),
        session_key=sd.SessionKey.custom("draft", (request.user.id, str(draft_id))),
    )


async def make_account(request: NavigationRequest, destination: AccountDestination) -> ScreenOpening:
    screen = await make_account_workspace(request)
    if destination.tab is not None and destination.tab not in screen.available_tabs:
        return ScreenOpening(
            sd.Response[Any](
                text_node(tr(t"navigation-tab-unavailable = That account tab is unavailable.")), audience="personal"
            )
        )
    screen.initial_tab = destination.tab
    return ScreenOpening(sd.Response[Any](screen, audience="personal"))
