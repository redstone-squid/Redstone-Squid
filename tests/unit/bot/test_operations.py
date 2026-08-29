import pytest

from squid.bot.errors import is_error_presented
from squid.bot.operations import managed_result, run_command_operation
from squid.bot.ui import info_node
from squid_ui import ComponentsV2Target
from squid_ui.document import DocumentLike
from squid_ui_discord.testing import ContextHarness, MessageHarness, message_harness
from tests.support.discord import invocation_scope, make_layout_bot


def _context(message: MessageHarness) -> ContextHarness:
    return ContextHarness(message=message, bot=make_layout_bot())


async def test_command_operation_receives_the_initial_delivery_before_work_starts() -> None:
    message = message_harness()
    context = _context(message)
    seen: list[object] = []

    async def work(progress, receipt):
        seen.append(receipt.message)
        progress.report(info_node("Working", "Halfway"))
        return info_node("Done", "Complete")

    async with invocation_scope(context) as invocation:
        await run_command_operation(invocation, work)

    assert seen == [message.source]
    assert len(context.sends) == 1
    assert "Done" in str(message.edits[-1].kwargs["view"].to_components())


async def test_command_operation_renders_and_rethrows_failure_once() -> None:
    message = message_harness()
    context = _context(message)
    error = RuntimeError("private")

    async def fail(_progress, _receipt):
        raise error

    async with invocation_scope(context) as invocation:
        with pytest.raises(RuntimeError, match="private"):
            await run_command_operation(invocation, fail)

    assert "Something went wrong" in str(message.edits[-1].kwargs["view"].to_components())
    assert is_error_presented(error)


async def test_command_operation_suppresses_a_terminal_scene_equal_to_its_initial_scene() -> None:
    message = message_harness()
    context = _context(message)

    async def adopt_external_card(_progress, _receipt):
        return info_node("Working", "Getting information...")

    async with invocation_scope(context) as invocation:
        await run_command_operation(invocation, adopt_external_card)

    assert message.edits == []


async def test_managed_result_invokes_command_and_renders_its_return_value() -> None:
    message = message_harness()
    ctx = _context(message)
    seen: list[tuple[object, int]] = []

    class Handler:
        @managed_result
        async def command(self, context: object, value: int) -> DocumentLike[ComponentsV2Target]:
            seen.append((context, value))
            return info_node("Done", "Complete")

    async with invocation_scope(ctx):
        await Handler().command(ctx, 42)

    assert seen == [(ctx, 42)]
    assert len(ctx.sends) == 1
    assert "Done" in str(message.edits[-1].kwargs["view"].to_components())


async def test_managed_result_requires_dispatch_invocation_scope() -> None:
    context = _context(message_harness())

    class Handler:
        @managed_result
        async def command(self, ctx: object) -> DocumentLike[ComponentsV2Target]:
            del ctx
            return info_node("Done", "Complete")

    with pytest.raises(RuntimeError, match="ambient invocation"):
        await Handler().command(context)
