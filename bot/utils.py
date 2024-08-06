from __future__ import annotations

import re
from traceback import format_tb
from types import TracebackType
from typing import overload, Literal

import discord
from async_lru import alru_cache
from discord import Message, Webhook
from discord.abc import Messageable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from bot.config import OWNER_ID, PRINT_TRACEBACKS
from database.database import DatabaseManager
from database.schema import DoorOrientationName, RecordCategory

discord_red = 0xF04747
discord_yellow = 0xFAA61A
discord_green = 0x43B581


def error_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=title, colour=discord_red, description=":x: " + description)


def warning_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=":warning: " + title, colour=discord_yellow, description=description)


def info_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=title, colour=discord_green, description=description)


def help_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=title, colour=discord_green, description=description)


@overload
def parse_dimensions(dim_str: str) -> tuple[int, int, int | None]: ...


@overload
def parse_dimensions(
    dim_str: str, *, min_dim: int, max_dim: Literal[3]
) -> tuple[int, int | None, int | None]: ...


def parse_dimensions(dim_str: str, *, min_dim: int = 2, max_dim: int = 3) -> tuple[int | None, ...]:
    """Parses a string representing dimensions. For example, '5x5' or '5x5x5'. Both 'x' and '*' are valid separators.

    Args:
        dim_str: The string to parse
        min_dim: The minimum number of dimensions
        max_dim: The maximum number of dimensions

    Returns:
        A list of the dimensions, the length of the list will be padded with None to match `max_dim`.
    """
    if min_dim > max_dim:
        raise ValueError(f"min_dim must be less than or equal to max_dim. Got {min_dim=} and {max_dim=}.")

    inputs_cross = dim_str.split("x")
    inputs_star = dim_str.split("*")
    if min_dim <= len(inputs_cross) <= max_dim:
        inputs = inputs_cross
    elif min_dim <= len(inputs_star) <= max_dim:
        inputs = inputs_star
    else:
        raise ValueError(
            f"Invalid number of dimensions. Expected {min_dim} to {max_dim} dimensions, found {len(inputs_cross)} in {dim_str=} splitting by 'x', and {len(inputs_star)} splitting by '*'."
        )

    try:
        dimensions = list(map(int, inputs))
    except ValueError:
        raise ValueError(f"Invalid input. Each dimension must be parsable as an integer, found {inputs}")

    # Pad with None
    return tuple(dimensions + [None] * (max_dim - len(dimensions)))


def parse_hallway_dimensions(dim_str: str) -> tuple[int | None, int | None, int | None]:
    """Parses a string representing the door's <size>, which essentially is the hallway's dimensions.

    Examples:
        "5x5x5" -> (5, 5, 5)
        "5x5" -> (5, 5, None)
        "5 wide" -> (5, None, None)
        "5 high" -> (None, 5, None)

    References:
        https://docs.google.com/document/d/1kDNXIvQ8uAMU5qRFXIk6nLxbVliIjcMu1MjHjLJrRH4/edit

    Returns:
        A tuple of the dimensions (width, height, depth).
    """
    try:
        return parse_dimensions(dim_str)
    except ValueError:
        if match := re.match(r"^(\d+)\s*(wide|high)$", dim_str):
            size, direction = match.groups()
            if direction == "wide":
                return int(size), None, None
            else:  # direction == "high"
                return None, int(size), None
        else:
            raise ValueError(
                "Invalid hallway size. Must be in the format 'width x height [x depth]' or '<width> wide' or '<height> high'"
            )


class RunningMessage:
    """Context manager to show a working message while the bot is working."""

    def __init__(
        self,
        ctx: Messageable | Webhook,
        *,
        title: str = "Working",
        description: str = "Getting information...",
        delete_on_exit: bool = False,
    ):
        self.ctx = ctx
        self.title = title
        self.description = description
        self.delete_on_exit = delete_on_exit
        self.sent_message: Message

    async def __aenter__(self) -> Message:
        sent_message = await self.ctx.send(embed=info_embed(self.title, self.description))
        if sent_message is None:
            raise ValueError(
                "Failed to send message. (You are probably sending a message to a webhook, try looking into Webhook.send)"
            )

        self.sent_message = sent_message
        return sent_message

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> bool:
        # Handle exceptions
        if exc_type is not None:
            description = f"{str(exc_val)}"
            if PRINT_TRACEBACKS:
                description += f'\n\n```{"".join(format_tb(exc_tb))}```'
            await self.sent_message.edit(
                content=f"<@{OWNER_ID}>",
                embed=error_embed(f"An error has occurred: {exc_type.__name__}", description),
            )
            return False

        # Handle normal exit
        if self.delete_on_exit:
            await self.sent_message.delete()
        return False


async def parse_build_title(title: str, mode: Literal["ai", "manual"] = "manual") -> tuple[DoorTitle, str]:
    """Parses a title into a category and a name.

    A build title should be in the format of:
    ```
    [Record Category] [component restrictions]+ <door size> [wiring placement restrictions]+ <door type>+ <orientation>
    ```

    Args:
        title: The title to parse
        mode: The mode to parse the title in. Either "ai" or "manual".

    Returns:
        A dictionary containing the parsed information.
    """
    if mode == "ai":
        llm = ChatOpenAI(model="gpt-4o-mini")
        raise NotImplementedError
    elif mode == "manual":
        return parse_piston_door_title(title)


class DoorTitle(BaseModel):
    record_category: RecordCategory | None = Field(..., description="")
    component_restrictions: list[str] = Field(..., description="")
    door_size: tuple[int | None, int | None, int | None] = Field(..., description="")
    wiring_placement_restrictions: list[str] = Field(..., description="")
    door_types: list[str] = Field(..., description="")
    orientation: DoorOrientationName | None = Field(..., description="")


valid_component_restrictions = ['No Slime Blocks', 'No Honey Blocks', 'No Gravity Blocks', 'No Sticky Pistons', 'Contained Slime Blocks', 'Contained Honey Blocks', 'Only Wiring Slime Blocks', 'Only Wiring Honey Blocks', 'Only Wiring Gravity Blocks', 'No Observers', 'No Note Blocks', 'No Clocks', 'No Entities', 'No Flying Machines', 'Zomba', 'Zombi', 'Torch and Dust Only', 'Redstone Block Only']
valid_wiring_placement_restrictions = ['Super Seamless', 'Full Seamless', 'Semi Seamless', 'Quart Seamless', 'Dentless', 'Full Trapdoor', 'Flush', 'Deluxe', 'Flush Layout', 'Semi Flush', 'Semi Deluxe', 'Full Floor Hipster', 'Full Ceiling Hipster', 'Full Wall Hipster', 'Semi Floor Hipster', 'Semi Ceiling Hipster', 'Semi Wall Hipster', 'Expandable', 'Full Tileable', 'Semi Tileable']
valid_door_types = ['Regular', 'Funnel', 'Asdjke', 'Cave', 'Corner', 'Dual Cave Corner', 'Staircase', 'Gold Play Button', 'Vortex', 'Pitch', 'Bar', 'Vertical', 'Yaw', 'Reversed', 'Inverted', 'Dual', 'Vault', 'Iris', 'Onion', 'Stargate', 'Full Lamp', 'Lamp', 'Hidden Lamp', 'Sissy Bar', 'Checkerboard', 'Windows', 'Redstone Block Center', 'Sand', 'Glass Stripe', 'Center Glass', 'Always On Lamp', 'Circle', 'Triangle', 'Right Triangle', 'Banana', 'Diamond', 'Slab-Shifted', 'Rail', 'Dual Rail', 'Carpet', 'Semi TNT', 'Full TNT']


def replace_insensitive(string: str, old: str, new: str) -> str:
    """Replaces a substring in a string case-insensitively.

    Args:
        string: The string to search and replace in.
        old: The substring to search for.
        new: The substring to replace with.

    Returns:
        The modified string.
    """
    pattern = re.compile(re.escape(old), re.IGNORECASE)
    return pattern.sub(new, string)


def parse_piston_door_title(title: str) -> tuple[DoorTitle, str]:
    title = title.lower()

    # Define record categories
    record_categories = ["smallest", "fastest", "first"]

    # Check for record category
    record_category = None
    for category in record_categories:
        if title.startswith(category):
            record_category = category.capitalize()
            title = title[len(category):].strip()
            break

    # Extract door size
    door_size_match = re.search(r'\d+x\d+(x\d+)?', title)
    door_size = (None, None, None)
    if door_size_match:
        door_size_str = door_size_match.group()
        door_size = tuple(map(int, door_size_str.split('x')))
        if len(door_size) == 2:
            door_size = (*door_size, None)
        title = replace_insensitive(title, door_size_str, '').strip()

    # Split the remaining title by known door types
    door_types = []
    for door_type in valid_door_types:
        if door_type.lower() in title.lower():
            door_types.append(door_type)
            title = replace_insensitive(title, door_type, '').strip()

    # Split remaining by orientation
    orientation: DoorOrientationName | None = None
    for orient in ["Door", "Skydoor", "Trapdoor"]:
        if orient.lower() in title:
            orientation = orient
            title = replace_insensitive(title, orient, '').strip()
            break

    # Remaining words are restrictions
    words = title.split()

    component_restrictions = []
    wiring_placement_restrictions = []
    unparsed = []
    for word in words:
        if word.title() in valid_component_restrictions:
            component_restrictions.append(word.title())
        elif word.title() in valid_wiring_placement_restrictions:
            wiring_placement_restrictions.append(word.title())
        else:
            unparsed.append(word)

    return DoorTitle(
        record_category=record_category,
        component_restrictions=component_restrictions,
        door_size=door_size,
        wiring_placement_restrictions=wiring_placement_restrictions,
        door_types=door_types,
        orientation=orientation
    ), ', '.join(unparsed)


# --- Unused ---
@alru_cache()
async def get_valid_restrictions(type: Literal["component", "wiring-placement"]) -> list[str]:
    """Gets a list of valid restrictions for a given type.

    Args:
        type: The type of restriction. Either "component" or "wiring-placement"

    Returns:
        A list of valid restrictions for the given type.
    """
    db = DatabaseManager()
    valid_restrictions_response = await db.table("restrictions").select("name").eq("type", type).execute()
    return [restriction["name"] for restriction in valid_restrictions_response.data]


async def validate_restrictions(restrictions: list[str], type: Literal["component", "wiring-placement"]) -> list[str]:
    """Validates a list of restrictions to ensure all of them are valid.

    Args:
        restrictions: The list of restrictions to validate
        type: The type of restriction. Either "component" or "wiring_placement"

    Returns:
        The original list of restrictions if all of them are valid.

    Raises:
        ValueError: If any of the restrictions are invalid.
    """
    valid_restrictions = await get_valid_restrictions(type)

    invalid_restrictions = [r for r in restrictions if r not in valid_restrictions]
    if invalid_restrictions:
        raise ValueError(f"Invalid {type} restrictions. Found {invalid_restrictions} which are not one of the restrictions in the database.")
    return restrictions

async def validate_door_types(door_types: list[str]) -> list[str]:
    """Validates a list of door types to ensure all of them are valid.

    Args:
        door_types: The list of door types to validate

    Returns:
        The original list of door types if all of them are valid.

    Raises:
        ValueError: If any of the door types are invalid.
    """
    db = DatabaseManager()
    valid_door_types_response = await db.table("types").select("name").eq("build_category", "Door").execute()
    valid_door_types_in_db = [door_type["name"] for door_type in valid_door_types_response.data]
    invalid_door_types = [dt for dt in door_types if dt not in valid_door_types_in_db]
    if invalid_door_types:
        raise ValueError(f"Invalid door types. Found {invalid_door_types} which are not one of the door types in the database.")
    return door_types
