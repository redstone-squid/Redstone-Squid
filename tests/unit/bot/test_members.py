"""Bulk membership distinguishes absence from failure and avoids redundant requests."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from squid.bot.members import DiscordMembers


async def test_gateway_batches_only_uncached_unique_ids() -> None:
    client = Mock(spec=discord.Client)
    client.is_ws_ratelimited.return_value = False
    guild = Mock(spec=discord.Guild)
    cached = cast(discord.Member, SimpleNamespace(id=1))
    guild.get_member.side_effect = lambda user_id: cached if user_id == 1 else None

    async def query(*, user_ids: list[int], limit: int, cache: bool) -> list[Any]:
        assert limit == 100
        assert cache
        return [SimpleNamespace(id=user_id) for user_id in user_ids if user_id != 3]

    guild.query_members = AsyncMock(side_effect=query)
    result = await DiscordMembers(client).resolve(guild, [*range(1, 205), 1, 2])

    assert result[1] is cached
    assert result[3] is None
    assert len(result) == 204
    assert [len(call.kwargs["user_ids"]) for call in guild.query_members.await_args_list] == [100, 100, 3]
    guild.fetch_member.assert_not_called()


async def test_failed_gateway_batch_does_not_turn_unknown_members_into_absences() -> None:
    client = Mock(spec=discord.Client)
    client.is_ws_ratelimited.return_value = False
    guild = Mock(spec=discord.Guild)
    cached = cast(discord.Member, SimpleNamespace(id=1))
    guild.get_member.side_effect = lambda user_id: cached if user_id == 1 else None
    guild.query_members = AsyncMock(side_effect=TimeoutError)

    assert await DiscordMembers(client).resolve(guild, [1, 2, 3]) == {1: cached}


@pytest.mark.parametrize("ratelimited", [False, True])
async def test_single_member_uses_rest_and_only_404_confirms_absence(ratelimited: bool) -> None:
    client = Mock(spec=discord.Client)
    client.is_ws_ratelimited.return_value = ratelimited
    guild = Mock(spec=discord.Guild)
    guild.get_member.return_value = None
    resolver = DiscordMembers(client)
    guild.fetch_member = AsyncMock(side_effect=discord.NotFound(Mock(status=404, reason=""), "gone"))
    assert await resolver.resolve(guild, [1]) == {1: None}
    guild.fetch_member.side_effect = discord.Forbidden(Mock(status=403, reason=""), "denied")
    assert await resolver.resolve(guild, [1]) == {}
    guild.fetch_member.side_effect = discord.HTTPException(Mock(status=503, reason=""), "unavailable")
    assert await resolver.resolve(guild, [1]) == {}
    guild.query_members.assert_not_called()


async def test_gateway_rate_limit_falls_back_to_rest() -> None:
    client = Mock(spec=discord.Client)
    client.is_ws_ratelimited.return_value = True
    guild = Mock(spec=discord.Guild)
    guild.get_member.return_value = None
    member = cast(discord.Member, SimpleNamespace(id=1))
    guild.fetch_member = AsyncMock(side_effect=[member, discord.NotFound(Mock(status=404, reason=""), "gone")])

    assert await DiscordMembers(client).resolve(guild, [1, 2]) == {1: member, 2: None}
    guild.query_members.assert_not_called()
