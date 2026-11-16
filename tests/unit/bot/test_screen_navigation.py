"""All entrypoints resolve a route with the opening user's authority."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

import squid_ui.ext.discord as sd
from squid.bot.accounts.workspace import AccountWorkspace
from squid.bot.navigation.assembly import build_screen_router
from squid.bot.navigation.commands import NavigationCommands, setup
from squid.bot.navigation.controls import destination_button, open_address
from squid.bot.navigation.router import ScreenRouter, ScreenUnavailable
from squid.bot.submission.build_browse import BuildBrowseScreen
from squid.bot.submission.submit import SavedDraftsScreen
from squid.bot.submission.ui import drafts
from squid.bot.submission.ui.controls import reopen_draft
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.builds.domain import Status
from squid.submissions.errors import DraftAccessDeniedError, DraftNotFoundError
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.discord.testing import InteractionHarness
from tests.support.discord import make_layout_bot


@pytest.mark.parametrize("address", ["/builds", "/builds/12", "/drafts", "/drafts?inbox=true", "/account?tab=claims"])
def test_real_assembly_roundtrips(address: str) -> None:
    router = build_screen_router()
    match = router.parse(address)
    assert router.url_for(match.route, **match.arguments) == address


def test_every_screen_route_is_registered_and_addressable() -> None:
    router = build_screen_router()
    for screen in (BuildBrowseScreen, SavedDraftsScreen, DraftEditorScreen, AccountWorkspace):
        with pytest.raises(RuntimeError, match="frozen"):
            router.register(screen)


def test_persistent_button_is_self_contained_and_bounded() -> None:
    router = build_screen_router()
    button = destination_button(router, "Open", DraftEditorScreen.open_draft, draft_id=UUID(int=1))
    assert button.route_id == open_address.id(address="/drafts/00000000-0000-0000-0000-000000000001")
    with pytest.raises(LayoutInvariantError, match="100"):
        destination_button(router, "Open", BuildBrowseScreen.open_detail, build_id=10**100)


@pytest.mark.parametrize("entrypoint", ["command", "button", "legacy"])
async def test_entrypoints_use_the_current_router(entrypoint: str) -> None:
    bot = make_layout_bot()
    harness = InteractionHarness(user_id=77, client=bot)
    router = build_screen_router()
    router.open = AsyncMock(return_value=None)  # type: ignore[method-assign]
    router.open_address = AsyncMock(return_value=None)  # type: ignore[method-assign]
    bot.screen_router = router
    draft_id = UUID(int=1)
    address = router.url_for(DraftEditorScreen.open_draft, draft_id=draft_id)
    if entrypoint == "command":
        await cast(Any, NavigationCommands).open_screen.callback(
            NavigationCommands(bot), harness.source, address=address
        )
    elif entrypoint == "button":
        await open_address(harness.source, address)
    else:
        await reopen_draft(harness.source, str(draft_id))
        router.open_address.assert_awaited_once()
        request, opened = router.open_address.call_args.args
        assert opened == address
        assert request.user.id == 77
        return
    router.open.assert_awaited_once()
    request, route = router.open.call_args.args
    assert route is DraftEditorScreen.open_draft
    assert router.open.call_args.kwargs == {"draft_id": draft_id}
    assert request.user.id == 77


async def test_setup_rebuilds_without_global_screen_registration() -> None:
    bot = make_layout_bot(add_cog=AsyncMock())
    await setup(bot)
    first = bot.screen_router
    await setup(bot)
    assert bot.screen_router is not first
    match = bot.screen_router.parse("/builds/12")
    assert match.route is BuildBrowseScreen.open_detail
    assert match.arguments == {"build_id": 12}


@pytest.mark.parametrize("status", [None, Status.PENDING, Status.CONFIRMED])
@pytest.mark.parametrize("view_pending", [False, True])
async def test_build_visibility_is_checked_before_opening(
    monkeypatch: pytest.MonkeyPatch,
    status: Status | None,
    view_pending: bool,
) -> None:
    request = MagicMock()
    screen = MagicMock(_capabilities=MagicMock(view_pending=view_pending))
    build = None if status is None else MagicMock(submission_status=status)
    request.client.services.build_queries.get = AsyncMock(return_value=build)
    monkeypatch.setattr(BuildBrowseScreen, "create", AsyncMock(return_value=screen))
    allowed = status is Status.CONFIRMED or (status is Status.PENDING and view_pending)
    if allowed:
        opening = await BuildBrowseScreen.open_detail(request, 12)
        assert opening is not None
        assert opening.screen is screen
    else:
        with pytest.raises(ScreenUnavailable):
            await BuildBrowseScreen.open_detail(request, 12)


async def test_draft_consent_stops_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drafts, "ensure_consented_account", AsyncMock(return_value=None))
    editor = AsyncMock()
    monkeypatch.setattr(drafts, "draft_editor", editor)
    assert await DraftEditorScreen.open_draft(MagicMock(), UUID(int=1)) is None
    editor.assert_not_awaited()


@pytest.mark.parametrize("error", [DraftNotFoundError(UUID(int=1)), DraftAccessDeniedError()])
async def test_draft_access_failure_does_not_open_editor(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    monkeypatch.setattr(drafts, "ensure_consented_account", AsyncMock(return_value=1))
    monkeypatch.setattr(drafts, "draft_editor", AsyncMock(side_effect=error))
    with pytest.raises(ScreenUnavailable):
        await DraftEditorScreen.open_draft(MagicMock(), UUID(int=1))


async def test_account_tab_validation_and_current_request(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = MagicMock(available_tabs={"identity"})
    create = AsyncMock(return_value=screen)
    monkeypatch.setattr(AccountWorkspace, "create", create)
    request = MagicMock()
    with pytest.raises(ScreenUnavailable):
        await AccountWorkspace.open_account(request, "review")
    opening = await AccountWorkspace.open_account(request, "identity")
    assert opening is not None
    assert opening.screen is screen
    assert screen.initial_tab == "identity"
    create.assert_awaited_with(request)


@pytest.mark.parametrize(("address", "deferral"), [("/builds", "private"), ("/drafts", "private")])
async def test_open_address_defers_with_the_screens_audience(address: str, deferral: str) -> None:
    bot = make_layout_bot()
    bot.screen_router = build_screen_router()
    bot.screen_router.open = AsyncMock(return_value=None)  # type: ignore[method-assign]
    harness = InteractionHarness(user_id=77, client=bot)
    await open_address(harness.source, address)
    assert harness.source.response.defer.await_args.kwargs["ephemeral"] is (deferral == "private")


def test_screen_router_type_is_exported() -> None:
    assert isinstance(build_screen_router(), ScreenRouter)
    assert sd.Screen
