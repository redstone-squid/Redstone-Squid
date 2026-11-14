"""All entrypoints resolve a destination with the opening user's authority."""

from dataclasses import dataclass
from typing import Any, assert_type, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

import squid_ui_discord as sd
from squid.bot.navigation import factories
from squid.bot.navigation.assembly import build_screen_router
from squid.bot.navigation.commands import NavigationCommands, setup
from squid.bot.navigation.controls import destination_button, open_address
from squid.bot.navigation.destinations import (
    AccountDestination,
    BuildDetail,
    BuildIndex,
    Destination,
    DraftDetail,
    DraftIndex,
)
from squid.bot.navigation.router import NavigationRequest, ScreenOpening, ScreenRouter, screen_route
from squid.bot.submission.build_browse import BuildBrowseScreen
from squid.bot.submission.ui.controls import reopen_draft
from squid.builds.domain import Status
from squid.submissions.errors import DraftAccessDeniedError, DraftNotFoundError
from squid_ui.errors import LayoutInvariantError
from squid_ui_discord.testing import InteractionHarness
from tests.support.discord import make_layout_bot


def test_class_decorator_preserves_concrete_type() -> None:
    @screen_route("/example", destination=BuildIndex)
    class Example(sd.Screen):
        def render(self) -> tuple[()]:
            return ()

        def example(self) -> int:
            return 1

    assert_type(Example().example(), int)


def test_factory_types() -> None:
    async def wrong_factory(request: NavigationRequest, destination: DraftIndex) -> ScreenOpening | None:
        return None

    router = ScreenRouter()
    # A different destination is an authoring error even when its runtime shape happens to match.
    router.register(BuildBrowseScreen, destination=BuildIndex, factory=wrong_factory)  # type: ignore[bad-argument-type]


@pytest.mark.parametrize("address", ["/builds", "/builds/12", "/drafts", "/drafts?inbox=true", "/account?tab=claims"])
def test_real_assembly_roundtrips(address: str) -> None:
    router = build_screen_router()
    assert router.format(router.parse(address)) == address


def test_persistent_button_is_self_contained_and_bounded() -> None:
    router = build_screen_router()
    button = destination_button(router, "Open", DraftDetail(UUID(int=1)))
    assert button.route_id == open_address.id(address="/drafts/00000000-0000-0000-0000-000000000001")
    with pytest.raises(LayoutInvariantError, match="100"):
        destination_button(router, "Open", BuildDetail(10**100))


@pytest.mark.parametrize("entrypoint", ["command", "button", "legacy"])
async def test_entrypoints_use_the_current_router(entrypoint: str) -> None:
    bot = make_layout_bot()
    harness = InteractionHarness(user_id=77, client=bot)
    router = build_screen_router()
    router.open = AsyncMock(return_value=None)  # type: ignore[method-assign]
    bot.screen_router = router
    destination = DraftDetail(UUID(int=1))
    address = router.format(destination)
    if entrypoint == "command":
        await cast(Any, NavigationCommands).open_screen.callback(
            NavigationCommands(bot), harness.source, address=address
        )
    elif entrypoint == "button":
        await open_address(harness.source, address)
    else:
        await reopen_draft(harness.source, str(destination.draft_id))
    router.open.assert_awaited_once()
    request, resolved = router.open.call_args.args
    assert resolved == destination
    assert request.user.id == 77


async def test_setup_rebuilds_without_global_screen_registration() -> None:
    bot = make_layout_bot(add_cog=AsyncMock())
    await setup(bot)
    first = bot.screen_router
    await setup(bot)
    assert bot.screen_router is not first
    assert bot.screen_router.parse("/builds/12") == BuildDetail(12)


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
    monkeypatch.setattr(factories, "make_catalogue", AsyncMock(return_value=screen))
    opening = await factories.make_build_detail(request, BuildDetail(12))
    allowed = status is Status.CONFIRMED or (status is Status.PENDING and view_pending)
    assert (opening.response.content is screen) is allowed


async def test_draft_consent_stops_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factories, "ensure_consented_account", AsyncMock(return_value=None))
    editor = AsyncMock()
    monkeypatch.setattr(factories, "draft_editor", editor)
    assert await factories.make_draft_detail(MagicMock(), DraftDetail(UUID(int=1))) is None
    editor.assert_not_awaited()


@pytest.mark.parametrize("error", [DraftNotFoundError(UUID(int=1)), DraftAccessDeniedError()])
async def test_draft_access_failure_does_not_open_editor(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    monkeypatch.setattr(factories, "ensure_consented_account", AsyncMock(return_value=1))
    monkeypatch.setattr(factories, "draft_editor", AsyncMock(side_effect=error))
    opening = await factories.make_draft_detail(MagicMock(), DraftDetail(UUID(int=1)))
    assert opening is not None
    assert "unavailable" in str(opening.response.content)


async def test_account_tab_validation_and_current_request(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = MagicMock(available_tabs={"identity"})
    factory = AsyncMock(return_value=screen)
    monkeypatch.setattr(factories, "make_account_workspace", factory)
    request = MagicMock()
    unavailable = await factories.make_account(request, AccountDestination("review"))
    assert "unavailable" in str(unavailable.response.content)
    opening = await factories.make_account(request, AccountDestination("identity"))
    assert opening.response.content is screen
    assert screen.initial_tab == "identity"
    factory.assert_awaited_with(request)


@pytest.mark.parametrize("query", [("missing",), ("build_id",)])
def test_registration_rejects_bad_field_mapping(query: tuple[str, ...]) -> None:
    @screen_route("/builds/{build_id}", destination=BuildDetail, query=query)
    class Invalid(sd.Screen):
        pass

    with pytest.raises(ValueError, match="exactly once"):
        ScreenRouter().register(Invalid, destination=BuildDetail, factory=AsyncMock())


def test_registration_requires_constant_query_defaults() -> None:
    @dataclass(frozen=True)
    class RequiredQuery(Destination):
        tab: int

    @screen_route("/required", destination=RequiredQuery, query=("tab",))
    class Invalid(sd.Screen):
        pass

    with pytest.raises(ValueError, match="defaults"):
        ScreenRouter().register(Invalid, destination=RequiredQuery, factory=AsyncMock())
