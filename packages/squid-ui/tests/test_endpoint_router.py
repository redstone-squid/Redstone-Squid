"""Screen factories and actions share identities, invocation, and persistent addresses."""

from typing import Any, Literal
from uuid import UUID

import pytest

from squid_ui.errors import LayoutInvariantError
from squid_ui.routing import InvalidAddress, Proceed, RouteMatch, Router


async def test_class_and_function_endpoints_share_dispatch_and_middleware() -> None:
    router = Router[list[str]]()

    async def audit(context: list[str], match: RouteMatch[list[str]], proceed: Proceed) -> Any:
        context.append(match.endpoint.path)
        return await proceed()

    router.add_middleware(audit)

    class Screen:
        @router.route("/account", fragment="tab")
        @classmethod
        async def open(cls, context: list[str], tab: Literal["claims"] | None = None) -> tuple[type, str | None]:
            return cls, tab

    @router.route("/builds/{build_id}/vote")
    async def vote(context: list[str], build_id: int) -> int:
        return build_id

    seen: list[str] = []
    assert await Screen.open(seen, tab="claims") == (Screen, "claims")
    assert await router.dispatch(seen, router.resolve("/builds/12/vote")) == 12
    assert seen == ["/account", "/builds/{build_id}/vote"]
    assert Screen.open.custom_id(tab="claims") == "ui:/account#claims"
    assert vote.custom_id(build_id=12) == "ui:/builds/12/vote"
    assert router.resolve(Screen.open.address()).endpoint is Screen.open


async def test_reload_updates_handles_and_keeps_inflight_handler() -> None:
    router = Router[object]()

    @router.route("/items/{id}")
    async def first(context: object, id: int) -> str:
        return "old"

    router.freeze()
    match = router.resolve("/items/2")

    @router.route("/items/{id}")
    async def second(context: object, id: int) -> str:
        return "new"

    assert first is second
    assert await first(object(), 2) == "new"
    assert await router.dispatch(object(), match) == "new"
    with pytest.raises(RuntimeError, match="frozen"):
        router.route("/new")(second.function)
    with pytest.raises(ValueError, match="parameters"):

        @router.route("/items/{id}")
        async def incompatible(context: object, id: UUID) -> None:
            pass

    assert await first(object(), 2) == "new"


@pytest.mark.parametrize(
    "address", ["items/2", "//host/items/2", "/items/0", "/items/x", "/items/2?extra=true", "/items/2#bad"]
)
def test_invalid_addresses(address: str) -> None:
    router = Router[object]()

    @router.route("/items/{id}")
    async def item(context: object, id: int) -> None:
        pass

    with pytest.raises(InvalidAddress):
        router.resolve(address)
    with pytest.raises(LayoutInvariantError, match="100"):
        item.custom_id(id=10**100)


async def test_group_middleware_runs_for_direct_calls_and_can_short_circuit() -> None:
    router = Router[object]()
    group = router.group("/private")

    async def deny(context: object, match: RouteMatch[object], proceed: Proceed) -> str:
        return "denied"

    group.add_middleware(deny)

    @group.route("/action")
    async def action(context: object) -> None:
        pytest.fail("denied handlers must not run")

    assert await action(object()) == "denied"
    assert await router.dispatch(object(), router.resolve("/private/action")) == "denied"
    assert len(router.endpoints()) == 1
