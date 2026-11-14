"""Screen address declarations and resolution do not own Discord delivery scopes."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

import squid_ui_discord as sd
from squid.bot.navigation.destinations import (
    AccountDestination,
    BuildDetail,
    BuildIndex,
    Destination,
    DraftDetail,
    DraftIndex,
)
from squid.bot.navigation.router import InvalidAddress, ScreenOpening, ScreenRouter, screen_route


@screen_route("/builds", destination=BuildIndex)
@screen_route("/builds/{build_id}", destination=BuildDetail)
class Browser(sd.Screen):
    def render(self) -> tuple[()]:
        return ()


@screen_route("/drafts", destination=DraftIndex, query=("inbox",))
class Drafts(sd.Screen):
    pass


@screen_route("/drafts/{draft_id}", destination=DraftDetail)
class Draft(sd.Screen):
    pass


@screen_route("/account", destination=AccountDestination, query=("tab",))
class Account(sd.Screen):
    pass


@pytest.fixture
def router() -> ScreenRouter:
    router = ScreenRouter()
    for screen, destination in (
        (Browser, BuildIndex),
        (Browser, BuildDetail),
        (Drafts, DraftIndex),
        (Draft, DraftDetail),
        (Account, AccountDestination),
    ):
        router.register(screen, destination=destination, factory=AsyncMock())
    router.freeze()
    return router


@pytest.mark.parametrize(
    ("destination", "address"),
    [
        (BuildIndex(), "/builds"),
        (BuildDetail(12), "/builds/12"),
        (DraftIndex(), "/drafts"),
        (DraftIndex(inbox=True), "/drafts?inbox=true"),
        (DraftDetail(UUID(int=1)), "/drafts/00000000-0000-0000-0000-000000000001"),
        (AccountDestination(), "/account"),
        (AccountDestination("claims"), "/account?tab=claims"),
    ],
)
def test_round_trip(router: ScreenRouter, destination: Destination, address: str) -> None:
    assert router.format(destination) == address
    assert router.parse(address) == destination


@pytest.mark.parametrize(
    "address",
    [
        "builds",
        "https://example.org/builds",
        "//example.org/builds",
        "/builds/0",
        "/builds/-1",
        "/builds/abc",
        "/builds/1/",
        "/builds#",
        "/builds?other=1",
        "/drafts/not-a-uuid",
        "/drafts?inbox=1",
        "/drafts?inbox=true&inbox=false",
        "/account?tab=missing",
        "/account?tab",
        "/account?tab=",
        "/builds\n",
        "/%62uilds",
        "/unknown",
    ],
)
def test_invalid_addresses(router: ScreenRouter, address: str) -> None:
    with pytest.raises(InvalidAddress):
        router.parse(address)


def test_default_query_is_omitted(router: ScreenRouter) -> None:
    assert router.format(router.parse("/drafts?inbox=false")) == "/drafts"


def test_decorator_preserves_class_and_does_not_inherit_routes() -> None:
    class Child(Browser):
        pass

    assert screen_route("/builds", destination=BuildIndex)(Child) is Child
    router = ScreenRouter()
    with pytest.raises(ValueError, match="exact Screen"):
        router.register(Child, destination=BuildDetail, factory=AsyncMock())


def test_duplicates_and_ambiguous_paths() -> None:
    router = ScreenRouter()
    router.register(Browser, destination=BuildDetail, factory=AsyncMock())
    with pytest.raises(ValueError, match="already registered"):
        router.register(Browser, destination=BuildDetail, factory=AsyncMock())

    @dataclass(frozen=True)
    class Other(Destination):
        id: int

    @screen_route("/builds/{id}", destination=Other)
    class OtherScreen(sd.Screen):
        pass

    with pytest.raises(ValueError, match="Ambiguous"):
        router.register(OtherScreen, destination=Other, factory=AsyncMock())


def test_frozen(router: ScreenRouter) -> None:
    with pytest.raises(RuntimeError, match="frozen"):
        router.register(Browser, destination=BuildIndex, factory=AsyncMock())


async def test_resolution_and_delivery() -> None:
    request = MagicMock(spec=sd.Request, respond=AsyncMock())
    factory = AsyncMock(side_effect=lambda request, destination: ScreenOpening(sd.Response(Browser()), "session"))
    router = ScreenRouter()
    router.register(Browser, destination=BuildIndex, factory=factory)
    first = await router.resolve(request, BuildIndex())
    second = await router.resolve(request, BuildIndex())
    assert first is not None
    assert second is not None
    assert first.response.content is not second.response.content
    request.respond.assert_not_awaited()
    await router.open(request, BuildIndex())
    assert request.respond.await_count == 1
    assert request.respond.call_args.kwargs == {"session_key": "session"}
    factory.return_value = None
    factory.side_effect = None
    await router.open(request, BuildIndex())
    assert request.respond.await_count == 1
