import inspect
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from discord.abc import Messageable

from squid.bot.errors import is_error_presented
from squid.bot.operations import managed_result, run_command_operation
from squid.bot.ui import info_node
from squid_ui import ComponentsV2Target
from squid_ui.document import DocumentLike
from squid_ui_discord.testing import message_harness
from tests.helpers.discord import invocation_scope, make_layout_bot


def _target(message: object) -> tuple[Messageable, AsyncMock]:
    send = AsyncMock(return_value=message)
    return cast(Messageable, SimpleNamespace(send=send)), send


def _context(target: Messageable, bot: object) -> object:
    return SimpleNamespace(bot=bot, send=target.send, interaction=None, author=SimpleNamespace(id=1), guild=None)


async def test_command_operation_receives_the_initial_delivery_before_work_starts() -> None:
    message = message_harness()
    target, send = _target(message)
    context = _context(target, make_layout_bot())
    seen: list[object] = []

    async def work(progress, receipt):
        seen.append(receipt.message)
        progress.report(info_node("Working", "Halfway"))
        return info_node("Done", "Complete")

    async with invocation_scope(context) as invocation:
        await run_command_operation(invocation, work)

    assert seen == [message]
    send.assert_awaited_once()
    assert "Done" in str(message.edit.await_args.kwargs["view"].to_components())


async def test_command_operation_renders_and_rethrows_failure_once() -> None:
    message = message_harness()
    target, _send = _target(message)
    context = _context(target, make_layout_bot())
    error = RuntimeError("private")

    async def fail(_progress, _receipt):
        raise error

    async with invocation_scope(context) as invocation:
        with pytest.raises(RuntimeError, match="private"):
            await run_command_operation(invocation, fail)

    assert "Something went wrong" in str(message.edit.await_args.kwargs["view"].to_components())
    assert is_error_presented(error)


async def test_command_operation_suppresses_a_terminal_scene_equal_to_its_initial_scene() -> None:
    message = message_harness()
    target, _send = _target(message)
    context = _context(target, make_layout_bot())

    async def adopt_external_card(_progress, _receipt):
        return info_node("Working", "Getting information...")

    async with invocation_scope(context) as invocation:
        await run_command_operation(invocation, adopt_external_card)

    message.edit.assert_not_awaited()


async def test_managed_result_keeps_the_command_signature_and_renders_its_return_value() -> None:
    message = message_harness()
    send = AsyncMock(return_value=message)
    ctx = SimpleNamespace(
        bot=make_layout_bot(),
        send=send,
        interaction=None,
        author=SimpleNamespace(id=1),
        guild=None,
    )
    seen: list[tuple[object, int]] = []

    class Handler:
        @managed_result
        async def command(self, context: object, value: int) -> DocumentLike[ComponentsV2Target]:
            seen.append((context, value))
            return info_node("Done", "Complete")

    assert (
        str(inspect.signature(Handler.command))
        == "(self, context: object, value: int) -> DocumentLike[squid_ui.target_types.ComponentsV2Target]"
    )

    async with invocation_scope(ctx):
        await Handler().command(ctx, 42)

    assert seen == [(ctx, 42)]
    send.assert_awaited_once()
    assert "Done" in str(message.edit.await_args.kwargs["view"].to_components())


async def test_managed_result_requires_dispatch_invocation_scope() -> None:
    context = _context(cast(Messageable, SimpleNamespace(send=AsyncMock())), make_layout_bot())

    class Handler:
        @managed_result
        async def command(self, ctx: object) -> DocumentLike[ComponentsV2Target]:
            del ctx
            return info_node("Done", "Complete")

    with pytest.raises(RuntimeError, match="ambient invocation"):
        await Handler().command(context)
