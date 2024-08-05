"""Handles user data and operations."""
from uuid import UUID

import requests

from utils import utcnow
from database import DatabaseManager


async def add_user(user_id: int = None, ign: str = None) -> int:
    """Add a user to the database.

    Args:
        user_id: The user's Discord ID.
        ign: The user's in-game name.

    Returns:
        The ID of the new user.
    """
    if user_id is None and ign is None:
        raise ValueError("No user data provided.")

    db = DatabaseManager()
    response = await db.table("users").insert({"discord_id": user_id, "ign": ign}).execute()
    return response.data[0]["id"]


async def link_minecraft_account(user_id: int, code: str) -> bool:
    """Using a verification code, link a user's Discord account with their Minecraft account.

    Args:
        user_id: The user's Discord ID.
        code: The verification code.

    Returns:
        True if the code is valid and the accounts are linked, False otherwise.
    """
    db = DatabaseManager()

    response = await db.table("verification_codes").select("minecraft_uuid", "minecraft_username").eq("code", code).gt("expires", utcnow()).maybe_single().execute()
    if response is None:
        return False
    minecraft_uuid = response.data["minecraft_uuid"]
    minecraft_username = response.data["minecraft_username"]

    # TODO: This currently does not check if the ign is already in use without a UUID or discord ID given.
    response = await db.table("users").update({"minecraft_uuid": minecraft_uuid, "ign": minecraft_username}).eq("discord_id", user_id).execute()
    if not response.data:
        await db.table("users").insert({"discord_id": user_id, "minecraft_uuid": minecraft_uuid, "ign": minecraft_username}).execute()
    return True


async def unlink_minecraft_account(user_id: int) -> bool:
    """Unlink a user's Minecraft account from their Discord account.

    Args:
        user_id: The user's Discord ID.

    Returns:
        True if the accounts were successfully unlinked, False otherwise.
    """
    db = DatabaseManager()
    await db.table("users").update({"minecraft_uuid": None}).eq("discord_id", user_id).execute()
    return True


def get_minecraft_username(user_uuid: str | UUID) -> str | None:
    """Get a user's Minecraft username from their UUID.

    Args:
        user_uuid: The user's Minecraft UUID.

    Returns:
        The user's Minecraft username. None if the UUID is invalid.
    """
    # https://wiki.vg/Mojang_API#UUID_to_Profile_and_Skin.2FCape
    response = requests.get(f"https://sessionserver.mojang.com/session/minecraft/profile/{str(user_uuid)}")
    if response.status_code == 200:
        return response.json()["name"]
    elif response.status_code == 204:  # No content
        return None
    else:
        raise ValueError(f"Failed to get username for UUID {user_uuid}.")
