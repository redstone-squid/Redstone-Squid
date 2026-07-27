"""Minecraft account lookup adapter."""

from uuid import UUID

import aiohttp


async def get_minecraft_username(minecraft_uuid: UUID) -> str | None:
    """Return the current username for a Minecraft UUID."""
    async with (
        aiohttp.ClientSession() as session,
        session.get(f"https://sessionserver.mojang.com/session/minecraft/profile/{minecraft_uuid!s}") as response,
    ):
        if response.status == 200:
            data = await response.json()
            return str(data["name"])
        if response.status == 204:
            return None
        msg = (
            f"Failed to get username for UUID {minecraft_uuid}. The Mojang API returned status code {response.status}."
        )
        raise ValueError(msg)
