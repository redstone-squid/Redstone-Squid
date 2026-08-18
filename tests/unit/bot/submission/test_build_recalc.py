"""The "Recalculate Build" context menu: who may run it, and on what."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from squid.bot.submission.submit import BuildSubmitCommands
from squid.bot.utils.permissions import PermissionNodeRequired
from squid.permissions.domain import Decision, Reason

BUILD_LOG_CHANNEL = 500


class StubPermissions:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed

    async def decisions(self, subject: Any, nodes: tuple[Any, ...]) -> tuple[Decision, ...]:
        return tuple(Decision(node=node.name, allowed=self.allowed, reason=Reason.DEFAULT) for node in nodes)


def _cog(*, allowed: bool = True) -> BuildSubmitCommands[Any]:
    cog = BuildSubmitCommands.__new__(BuildSubmitCommands)
    cog.bot = cast(
        Any,
        SimpleNamespace(
            services=SimpleNamespace(
                settings=SimpleNamespace(),
                accounts=SimpleNamespace(),
                permissions=StubPermissions(allowed=allowed),
            ),
            account_ids=SimpleNamespace(resolve=AsyncMock(return_value=1)),
            is_owner=AsyncMock(return_value=False),
            community_config=SimpleNamespace(build_log_channel_ids={BUILD_LOG_CHANNEL}),
        ),
    )
    cog.infer_build_from_message = AsyncMock()  # type: ignore[method-assign]
    return cog


def _interaction() -> discord.Interaction[Any]:
    return cast(
        discord.Interaction[Any],
        cast(
            Any,
            SimpleNamespace(
                user=SimpleNamespace(id=7),
                guild=None,
                guild_id=None,
                guild_locale=None,
                locale="en-US",
                client=None,
                response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
                followup=SimpleNamespace(send=AsyncMock(return_value=None)),
            ),
        ),
    )


def _message(*, channel_id: int = BUILD_LOG_CHANNEL, from_bot: bool = False) -> discord.Message:
    # A real TextChannel, because the eligibility check is an isinstance test: inference reads
    # the channel's history, which a thread or a DM does not offer the same way.
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    return cast(
        discord.Message,
        cast(Any, SimpleNamespace(author=SimpleNamespace(bot=from_bot), channel=channel)),
    )


async def _run(cog: BuildSubmitCommands[Any], message: discord.Message) -> discord.Interaction[Any]:
    interaction = _interaction()
    cast(Any, interaction).client = cog.bot
    await cog.recalc_context_menu(interaction, message)
    return interaction


async def test_the_menu_denies_the_way_the_command_did() -> None:
    """A context menu cannot carry `requires(...)`, so it raises what the decorator raises
    and the shared presenter renders one refusal for both surfaces."""
    cog = _cog(allowed=False)

    with pytest.raises(PermissionNodeRequired) as denial:
        await _run(cog, _message())

    assert denial.value.nodes == ("build.submission.recalc",)
    cast(Any, cog.infer_build_from_message).assert_not_awaited()


async def test_a_message_no_build_can_come_from_says_so() -> None:
    """The command reported "Build recalculated." whatever it was pointed at, because
    inference is a listener that silently ignores anything outside a build log channel."""
    cog = _cog()

    interaction = await _run(cog, _message(channel_id=999))

    cast(Any, cog.infer_build_from_message).assert_not_awaited()
    assert cast(Any, interaction).followup.send.await_count == 1


async def test_a_build_log_message_is_recalculated() -> None:
    cog = _cog()
    message = _message()

    await _run(cog, message)

    cast(Any, cog.infer_build_from_message).assert_awaited_once_with(message)
