"""Resolve cached Discord members and batch gateway lookups for missing IDs."""

import logging
from collections.abc import Collection
from itertools import batched

import anyio
import discord

logger = logging.getLogger(__name__)


class DiscordMembers:
    """Keep confirmed absences distinct from lookup failures.

    Results map an ID to a member or a confirmed absence (`None`). IDs whose lookup
    failed are omitted. This service never supplies cached facts for authorization
    refreshes, which continue to fetch membership directly from Discord.
    """

    def __init__(self, client: discord.Client) -> None:
        self.client = client

    async def resolve(self, guild: discord.Guild, user_ids: Collection[int]) -> dict[int, discord.Member | None]:
        result: dict[int, discord.Member | None] = {}
        missing: list[int] = []
        for user_id in dict.fromkeys(user_ids):
            member = guild.get_member(user_id)
            if member is None:
                missing.append(user_id)
            else:
                result[user_id] = member
        for batch in batched(missing, 100, strict=False):
            if len(batch) == 1 or self.client.is_ws_ratelimited():
                for user_id in batch:
                    try:
                        with anyio.fail_after(10):
                            result[user_id] = await guild.fetch_member(user_id)
                    except discord.NotFound:
                        result[user_id] = None
                    except discord.DiscordException, TimeoutError, OSError:
                        logger.warning("Discord member lookup failed in guild %s", guild.id, exc_info=True)
            else:
                try:
                    with anyio.fail_after(10):
                        members = await guild.query_members(user_ids=list(batch), limit=100, cache=True)
                except discord.DiscordException, TimeoutError, OSError:
                    logger.warning("Discord member batch lookup failed in guild %s", guild.id, exc_info=True)
                else:
                    result.update(dict.fromkeys(batch))
                    result.update((member.id, member) for member in members)
        return result
