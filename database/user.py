"""Handles user data and operations."""
from database.database import DatabaseManager


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
