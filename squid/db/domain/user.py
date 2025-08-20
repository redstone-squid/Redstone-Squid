import uuid
from dataclasses import dataclass

import aiohttp


@dataclass(slots=True)
class User:
    """A user in the system, which can be linked to both Discord and Minecraft accounts."""

    id: int | None = None
    ign: str | None = None
    discord_id: int | None = None
    minecraft_uuid: uuid.UUID | None = None

    async def link_minecraft(self, minecraft_uuid: uuid.UUID):
        """Link the user with this Minecraft account.

        Args:
            minecraft_uuid: The user's Minecraft UUID.
        """
        minecraft_username = await get_minecraft_username(minecraft_uuid)
        self.minecraft_uuid = minecraft_uuid
        self.ign = minecraft_username

    def unlink_minecraft(self) -> None:
        """Unlink the user from their Minecraft account."""
        self.minecraft_uuid = None
        self.ign = None


async def get_minecraft_username(user_uuid: str | uuid.UUID) -> str | None:
    """Get a user's Minecraft username from their UUID.

    Args:
        user_uuid: The user's Minecraft UUID.

    Returns:
        The user's Minecraft username. None if the UUID is invalid.
    """
    # https://wiki.vg/Mojang_API#UUID_to_Profile_and_Skin.2FCape
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://sessionserver.mojang.com/session/minecraft/profile/{str(user_uuid)}"
        ) as response:
            if response.status == 200:
                data = await response.json()
                return data["name"]
            elif response.status == 204:  # No content
                return None
            else:
                raise ValueError(
                    f"Failed to get username for UUID {user_uuid}. The Mojang API returned status code {response.status}."
                )
