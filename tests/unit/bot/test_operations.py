import inspect
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from discord.abc import Messageable

from squid.bot.errors import is_error_presented
from squid.bot.operations import managed_result, run_command_operation
from squid.bot.ui import info_node
from squid_layouts.discord.testing import fake_message
from squid_layouts.runtime.component import RenderResult
from tests.helpers.discord import make_layout_bot


def _target(message: object) -> tuple[Messageable, AsyncMock]:
    send = AsyncMock(return_value=message)
    return cast(Messageable, SimpleNamespace(send=send)), send


async def test_command_operation_receives_the_initial_delivery_before_work_starts() -> None:
    message = fake_message()
    target, send = _target(message)
    seen: list[object] = []

    async def work(progress, receipt):
        seen.append(receipt.message)
        progress.set(info_node("Working", "Halfway"))
        return info_node("Done", "Complete")

    await run_command_operation(target, work, source=make_layout_bot())

    assert seen == [message]
    send.assert_awaited_once()
    assert "Done" in str(message.edit.await_args.kwargs["view"].to_components())


async def test_command_operation_renders_and_rethrows_failure_once() -> None:
    message = fake_message()
    target, _send = _target(message)
    error = RuntimeError("private")

    async def fail(_progress, _receipt):
        raise error

    with pytest.raises(RuntimeError, match="private"):
        await run_command_operation(target, fail, source=make_layout_bot())

    assert "Something went wrong" in str(message.edit.await_args.kwargs["view"].to_components())
    assert is_error_presented(error)


async def test_command_operation_suppresses_a_terminal_scene_equal_to_its_initial_scene() -> None:
    message = fake_message()
    target, _send = _target(message)

    async def adopt_external_card(_progress, _receipt):
        return info_node("Working", "Getting information...")

    await run_command_operation(target, adopt_external_card, source=make_layout_bot())

    message.edit.assert_not_awaited()


async def test_managed_result_keeps_the_command_signature_and_renders_its_return_value() -> None:
    message = fake_message()
    send = AsyncMock(return_value=message)
    ctx = SimpleNamespace(bot=make_layout_bot(), send=send, interaction=None, guild=None)
    seen: list[tuple[object, int]] = []

    class Handler:
        @managed_result
        async def command(self, context: object, value: int) -> RenderResult:
            seen.append((context, value))
            return info_node("Done", "Complete")

    assert str(inspect.signature(Handler.command)) == "(self, context: object, value: int) -> RenderResult"

    await Handler().command(ctx, 42)

    assert seen == [(ctx, 42)]
    send.assert_awaited_once()
    assert "Done" in str(message.edit.await_args.kwargs["view"].to_components())
