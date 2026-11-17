"""Persistent buttons and direct endpoint calls share the application's delivery path."""

from typing import Literal, Self, override
from unittest.mock import AsyncMock, MagicMock

import anyio
import discord
import pytest

import squid_ui.ext.discord as sd
from squid.bot.routes import AppRequest, deliver
from squid_ui.ext.discord.routing.adapter import install
from squid_ui.ext.discord.testing import InteractionHarness
from squid_ui.routing import Router
from tests.support.discord import make_layout_bot


async def test_button_and_direct_call_dispatch_the_same_endpoint() -> None:
    router = Router[AppRequest]()
    router.add_middleware(deliver)
    seen: list[tuple[int, int]] = []

    @router.route("/vote/{build_id}")
    async def vote(request: AppRequest, build_id: int) -> sd.Response:
        seen.append((request.user.id, build_id))
        return sd.Response("Recorded.")

    bot = make_layout_bot(add_dynamic_items=MagicMock())
    errors = AsyncMock()
    install(router, bot, on_error=errors)
    install(router, bot, on_error=errors)
    bot.add_dynamic_items.assert_called_once()
    dispatch_type = bot.add_dynamic_items.call_args.args[0]
    button = dispatch_type(discord.ui.Button(custom_id=vote.custom_id(build_id=12)))
    clicked = InteractionHarness(user_id=77, client=bot)
    await button.callback(clicked.source)
    direct = InteractionHarness(user_id=77, client=bot)
    await vote(await sd.request(direct.source), 12)
    assert seen == [(77, 12), (77, 12)]
    clicked.response.send_message.assert_awaited_once()
    direct.response.send_message.assert_awaited_once()
    errors.assert_not_awaited()
    with pytest.raises(ValueError, match="already has"):
        install(Router[AppRequest](), bot, on_error=errors)


async def test_screen_endpoint_is_delivered_without_a_forwarding_route() -> None:
    router = Router[AppRequest]()
    router.add_middleware(deliver)

    class Screen(sd.Screen):
        @override
        def render(self) -> tuple[()]:
            return ()

        @router.route("/screen")
        @classmethod
        async def open(cls, request: AppRequest) -> Self:
            return cls()

    bot = make_layout_bot(add_dynamic_items=MagicMock())
    errors = AsyncMock()
    install(router, bot, on_error=errors)
    dispatch_type = bot.add_dynamic_items.call_args.args[0]
    clicked = InteractionHarness(user_id=77, client=bot)
    await dispatch_type(discord.ui.Button(custom_id=Screen.open.custom_id())).callback(clicked.source)
    errors.assert_not_awaited()
    clicked.response.send_message.assert_awaited_once()
    assert len(router.endpoints()) == 1


async def test_unknown_address_has_a_private_retired_control_reply() -> None:
    bot = make_layout_bot(add_dynamic_items=MagicMock())
    errors = AsyncMock()
    install(Router[AppRequest](), bot, on_error=errors)
    dispatch_type = bot.add_dynamic_items.call_args.args[0]
    clicked = InteractionHarness(user_id=77, client=bot)
    await dispatch_type(discord.ui.Button(custom_id="ui:/removed")).callback(clicked.source)
    assert clicked.response.send_message.await_args.kwargs["ephemeral"] is True
    errors.assert_not_awaited()


@pytest.mark.parametrize("audience", ["public", "personal"])
async def test_watchdog_uses_endpoint_policy_before_delivery(audience: Literal["public", "personal"]) -> None:
    router = Router[AppRequest]()
    router.add_middleware(deliver)

    @router.route("/slow", policy=sd.ResponseSpec(audience=audience))
    async def slow(request: AppRequest) -> sd.Response:
        await anyio.sleep(2.6)
        return sd.Response("Done.")

    clicked = InteractionHarness(user_id=77, client=make_layout_bot())
    await slow(await sd.request(clicked.source))
    assert clicked.response.defer.await_args.kwargs["ephemeral"] is (audience == "personal")


async def test_domain_errors_are_not_wrapped_by_acknowledgement_task_group() -> None:
    router = Router[AppRequest]()
    router.add_middleware(deliver)

    @router.route("/failed")
    async def failed(request: AppRequest) -> None:
        raise ValueError("failed operation")

    clicked = InteractionHarness(user_id=77, client=make_layout_bot())
    with pytest.raises(ValueError, match="failed operation"):
        await failed(await sd.request(clicked.source))
