"""Screen routes are declared on opening classmethods; the router does not own Discord delivery scopes."""

from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from asyncpg.pgproto.pgproto import UUID as AsyncpgUUID

import squid_ui.ext.discord as sd
from squid.bot.navigation.router import (
    InvalidAddress,
    NavigationRequest,
    ScreenOpening,
    ScreenRoute,
    ScreenRouter,
    ScreenUnavailable,
    screen_route,
)

type OptionalTab = Literal["identity", "claims"] | None


class Browser(sd.Screen):
    def render(self) -> tuple[()]:
        return ()

    @screen_route("/builds")
    @classmethod
    async def index(cls, request: NavigationRequest) -> ScreenOpening:
        return ScreenOpening(cls())

    @screen_route("/builds/{build_id}")
    @classmethod
    async def detail(cls, request: NavigationRequest, build_id: int) -> ScreenOpening:
        return ScreenOpening(cls(), session_key=build_id)


class Drafts(sd.Screen):
    @screen_route("/drafts")
    @classmethod
    async def index(cls, request: NavigationRequest, inbox: bool = False) -> ScreenOpening:
        return ScreenOpening(cls())

    @screen_route("/drafts/{draft_id}")
    @classmethod
    async def detail(cls, request: NavigationRequest, draft_id: UUID) -> ScreenOpening:
        return ScreenOpening(cls())


class Account(sd.Screen):
    @screen_route("/account", fragment="tab")
    @classmethod
    async def index(cls, request: NavigationRequest, tab: OptionalTab = None) -> ScreenOpening:
        return ScreenOpening(cls())


@pytest.fixture
def router() -> ScreenRouter:
    router = ScreenRouter()
    router.register(Browser, Drafts, Account)
    router.freeze()
    return router


@pytest.mark.parametrize(
    ("route", "arguments", "address"),
    [
        (Browser.index, {}, "/builds"),
        (Browser.detail, {"build_id": 12}, "/builds/12"),
        (Drafts.index, {}, "/drafts"),
        (Drafts.index, {"inbox": True}, "/drafts?inbox=true"),
        (Drafts.detail, {"draft_id": UUID(int=1)}, "/drafts/00000000-0000-0000-0000-000000000001"),
        (
            Drafts.detail,
            {"draft_id": AsyncpgUUID("00000000-0000-0000-0000-000000000001")},
            "/drafts/00000000-0000-0000-0000-000000000001",
        ),
        (Account.index, {}, "/account"),
        (Account.index, {"tab": "claims"}, "/account#claims"),
    ],
)
def test_round_trip(router: ScreenRouter, route: ScreenRoute[...], arguments: dict[str, Any], address: str) -> None:
    assert router.url_for(route, **arguments) == address
    match = router.parse(address)
    assert match.route is route
    # Parsed arguments fill in the defaults the address omits.
    defaults = {name: parameter.default for name, parameter in route.signature.parameters.items()}
    assert match.arguments == {**{k: v for k, v in defaults.items() if v is not route.signature.empty}, **arguments}


def test_parse_supplies_defaults(router: ScreenRouter) -> None:
    assert router.parse("/drafts").arguments == {"inbox": False}
    assert router.parse("/account").arguments == {"tab": None}


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
        "/account#missing",
        "/account#",
        "/account#claims#identity",
        "/account#%FF",
        "/account#%zz",
        "/account?tab=claims",
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


@pytest.mark.parametrize(
    ("route", "arguments"),
    [
        (Browser.detail, {"build_id": True}),
        (Browser.detail, {"build_id": 0}),
        (Browser.detail, {"build_id": "12"}),
        (Drafts.index, {"inbox": 1}),
        (Drafts.detail, {"draft_id": "00000000-0000-0000-0000-000000000001"}),
    ],
)
def test_invalid_arguments(router: ScreenRouter, route: ScreenRoute[...], arguments: dict[str, Any]) -> None:
    with pytest.raises(InvalidAddress):
        router.url_for(route, **arguments)


def test_missing_argument_is_a_programming_error(router: ScreenRouter) -> None:
    with pytest.raises(TypeError):
        router.url_for(Browser.detail)  # type: ignore[missing-argument]


def test_equal_integer_subclass_round_trip(router: ScreenRouter) -> None:
    class BuildId(int):
        pass

    assert router.url_for(Browser.detail, build_id=BuildId(12)) == "/builds/12"
    assert router.parse("/builds/12").arguments == {"build_id": BuildId(12)}


def test_default_query_is_omitted(router: ScreenRouter) -> None:
    match = router.parse("/drafts?inbox=false")
    assert router.url_for(match.route, **match.arguments) == "/drafts"


def test_routes_know_their_screen() -> None:
    assert Browser.detail.owner is Browser
    assert repr(Browser.detail) == "Browser.detail"


async def test_route_is_callable_like_the_classmethod() -> None:
    opening = await Browser.detail(MagicMock(), 7)
    assert opening is not None
    assert isinstance(opening.screen, Browser)
    assert opening.session_key == 7


def test_decorator_requires_a_classmethod() -> None:
    async def plain(cls: type[Any], request: NavigationRequest) -> ScreenOpening: ...

    with pytest.raises(TypeError, match="above @classmethod"):
        screen_route("/plain")(plain)  # type: ignore[bad-argument-type]


def test_registration_requires_routes_declared_on_the_exact_class() -> None:
    class Child(Browser):
        pass

    class Unroutable(sd.Screen):
        pass

    router = ScreenRouter()
    with pytest.raises(ValueError, match="no screen routes"):
        router.register(Child)
    with pytest.raises(ValueError, match="no screen routes"):
        router.register(Unroutable)


def test_duplicates_and_ambiguous_paths() -> None:
    router = ScreenRouter()
    router.register(Browser)
    with pytest.raises(ValueError, match="already registered"):
        router.register(Browser)

    class Other(sd.Screen):
        @screen_route("/builds/{id}")
        @classmethod
        async def detail(cls, request: NavigationRequest, id: int) -> ScreenOpening:
            return ScreenOpening(cls())

    with pytest.raises(ValueError, match="Ambiguous"):
        router.register(Other)


def test_frozen(router: ScreenRouter) -> None:
    with pytest.raises(RuntimeError, match="frozen"):
        router.register(Browser)


def test_url_for_requires_a_registered_route() -> None:
    with pytest.raises(InvalidAddress, match="Unregistered"):
        ScreenRouter().url_for(Browser.index)


def test_registration_rejects_path_parameters_the_opener_lacks() -> None:
    class Invalid(sd.Screen):
        @screen_route("/builds/{build_id}")
        @classmethod
        async def detail(cls, request: NavigationRequest, other: int = 1) -> ScreenOpening:
            return ScreenOpening(cls())

    with pytest.raises(ValueError, match="path parameter"):
        ScreenRouter().register(Invalid)


def test_registration_requires_constant_query_defaults() -> None:
    class Invalid(sd.Screen):
        @screen_route("/required")
        @classmethod
        async def index(cls, request: NavigationRequest, tab: int) -> ScreenOpening:
            return ScreenOpening(cls())

    with pytest.raises(ValueError, match="defaults"):
        ScreenRouter().register(Invalid)


def test_registration_requires_addressable_parameter_types() -> None:
    class Invalid(sd.Screen):
        @screen_route("/typed")
        @classmethod
        async def index(cls, request: NavigationRequest, tab: float = 1.0) -> ScreenOpening:
            return ScreenOpening(cls())

    with pytest.raises(ValueError, match="Unsupported"):
        ScreenRouter().register(Invalid)


async def test_open_delivers_the_screen_once_per_call(router: ScreenRouter) -> None:
    request = MagicMock(spec=sd.Request, respond=AsyncMock())
    await router.open(request, Browser.detail, build_id=3)
    await router.open(request, Browser.detail, build_id=3)
    first, second = (call.args[0] for call in request.respond.await_args_list)
    assert first is not second
    assert request.respond.call_args.kwargs == {"session_key": 3}


async def test_open_rejects_arguments_before_calling_the_opener(router: ScreenRouter) -> None:
    request = MagicMock(spec=sd.Request, respond=AsyncMock())
    with pytest.raises(InvalidAddress):
        await router.open(request, Browser.detail, build_id=0)
    request.respond.assert_not_awaited()


async def test_open_delivers_nothing_when_the_opener_handled_the_interaction() -> None:
    class Consenting(sd.Screen):
        @screen_route("/consent")
        @classmethod
        async def index(cls, request: NavigationRequest) -> ScreenOpening | None:
            return None

    router = ScreenRouter()
    router.register(Consenting)
    request = MagicMock(spec=sd.Request, respond=AsyncMock())
    assert await router.open(request, Consenting.index) is None
    request.respond.assert_not_awaited()


@pytest.mark.parametrize("audience", ["personal", "public"])
async def test_unavailable_is_delivered_under_the_screens_audience(audience: Literal["personal", "public"]) -> None:
    class Missing(sd.Screen):
        policy = sd.ResponseSpec(audience=audience)

        @screen_route("/missing")
        @classmethod
        async def index(cls, request: NavigationRequest) -> ScreenOpening:
            raise ScreenUnavailable("Nothing here.")

    router = ScreenRouter()
    router.register(Missing)
    request = MagicMock(spec=sd.Request, respond=AsyncMock())
    await router.open(request, Missing.index)
    assert request.respond.call_args.kwargs == {"audience": audience}


async def test_open_address_parses_then_opens(router: ScreenRouter) -> None:
    request = MagicMock(spec=sd.Request, respond=AsyncMock())
    await router.open_address(request, "/builds/5")
    assert request.respond.call_args.kwargs == {"session_key": 5}
    with pytest.raises(InvalidAddress):
        await router.open_address(request, "/builds/five")


def test_deferral_follows_the_screens_audience_policy() -> None:
    class Shared(sd.Screen):
        policy = sd.ResponseSpec(audience="public")

        @screen_route("/shared")
        @classmethod
        async def index(cls, request: NavigationRequest) -> ScreenOpening:
            return ScreenOpening(cls())

    router = ScreenRouter()
    router.register(Shared, Browser)
    assert router.deferral(Shared.index) == "public"
    # Screens default to a personal audience.
    assert router.deferral(Browser.index) == "private"


@pytest.mark.parametrize("fragment", ["missing", "build_id", "inbox"])
def test_fragment_must_name_an_optional_non_path_parameter(fragment: str) -> None:
    class Invalid(sd.Screen):
        @screen_route("/invalid/{build_id}", fragment=fragment)
        @classmethod
        async def detail(cls, request: NavigationRequest, build_id: int, inbox: bool = False) -> ScreenOpening:
            return ScreenOpening(cls())

    with pytest.raises(ValueError, match="Fragment"):
        ScreenRouter().register(Invalid)


def test_fragment_requires_optional_annotation() -> None:
    class Invalid(sd.Screen):
        @screen_route("/invalid", fragment="tab")
        @classmethod
        async def index(cls, request: NavigationRequest, tab: int = cast(int, None)) -> ScreenOpening:
            return ScreenOpening(cls())

    with pytest.raises(ValueError, match="optional type"):
        ScreenRouter().register(Invalid)


async def test_fragment_and_query_round_trip_and_reach_opener() -> None:
    class FocusScreen(sd.Screen):
        def render(self) -> tuple[()]:
            return ()

        @screen_route("/focus/{build_id}", fragment="location")
        @classmethod
        async def index(
            cls,
            request: NavigationRequest,
            build_id: int,
            inbox: bool = False,
            location: Literal["media/details", "a #?%é"] | None = None,
        ) -> ScreenOpening:
            assert (build_id, inbox, location) == (3, True, "a #?%é")
            return ScreenOpening(cls(), session_key=location)

    router = ScreenRouter()
    router.register(FocusScreen)
    address = router.url_for(FocusScreen.index, 3, inbox=True, location="a #?%é")
    assert address == "/focus/3?inbox=true#a%20%23%3F%25%C3%A9"
    assert router.parse(address).arguments == {"build_id": 3, "inbox": True, "location": "a #?%é"}
    request = MagicMock(spec=sd.Request, respond=AsyncMock())
    await router.open_address(request, address)
    assert request.respond.call_args.kwargs == {"session_key": "a #?%é"}


def test_empty_fragment_argument_is_not_addressable() -> None:
    class Empty(sd.Screen):
        @screen_route("/empty", fragment="target")
        @classmethod
        async def index(cls, request: NavigationRequest, target: Literal[""] | None = None) -> ScreenOpening:
            return ScreenOpening(cls())

    router = ScreenRouter()
    router.register(Empty)
    with pytest.raises(InvalidAddress, match="must not be empty"):
        router.url_for(Empty.index, target="")
