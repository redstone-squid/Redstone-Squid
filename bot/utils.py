"""Utility functions for the bot."""

from __future__ import annotations

import os
import re
from io import StringIO
from textwrap import dedent
from traceback import format_tb
from types import TracebackType
from typing import overload, Literal, TYPE_CHECKING, Any, Mapping, cast

import discord
from async_lru import alru_cache
from discord import Message, Webhook
from discord.abc import Messageable
from discord.ext.commands import Context, CommandError, NoPrivateMessage, MissingAnyRole, check
from markdown import Markdown
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from bot import config
from bot._types import GuildMessageable
from bot.config import OWNER_ID, PRINT_TRACEBACKS
from database import DatabaseManager
from database.schema import (
    DoorOrientationName,
    RecordCategory,
    DOOR_ORIENTATION_NAMES,
    MessageRecord,
    DeleteLogVoteSessionRecord,
)
from database.server_settings import get_server_setting

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

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
def parse_dimensions(dim_str: str, *, min_dim: int, max_dim: Literal[3]) -> tuple[int, int | None, int | None]: ...


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


def is_owner_server(ctx: Context[Any]):
    """Check if the command is executed on the owner's server."""

    if not ctx.guild or not ctx.guild.id == config.OWNER_SERVER_ID:
        # TODO: Make a custom error for this.
        # https://discordpy.readthedocs.io/en/stable/ext/commands/api.html?highlight=is_owner#discord.discord.ext.commands.on_command_error
        raise CommandError("This command can only be executed on certain servers.")
    return True


# See https://stackoverflow.com/questions/761824/python-how-to-convert-markdown-formatted-text-to-text
def _unmark_element(element: Element, stream=None):
    if stream is None:
        stream = StringIO()
    if element.text:
        stream.write(element.text)
    for sub in element:
        _unmark_element(sub, stream)
    if element.tail:
        stream.write(element.tail)
    return stream.getvalue()


# patching Markdown
Markdown.output_formats["plain"] = _unmark_element  # type: ignore
__md = Markdown(output_format="plain")  # type: ignore
__md.stripTopLevelTags = False


def remove_markdown(text: str) -> str:
    """Removes markdown formatting from a string."""
    return __md.convert(text)


async def parse_build_title(title: str, mode: Literal["ai", "manual"] = "manual") -> DoorTitle | None:
    """Parses a title into its components.

    A build title should be in the format of:
    ```
    [Record Category] [component restrictions]+ <door size> [wiring placement restrictions]+ <door type>+ <orientation>
    ```

    Args:
        title: The title to parse
        mode: The mode to parse the title in. Either "ai" or "manual".

    Returns:
        A tuple of the parsed door title and the unparsed part
    """
    if "\n" in title:
        raise ValueError("Title cannot contain newlines")

    if mode == "ai" and os.getenv("OPENAI_API_KEY"):
        return await ai_parse_piston_door_title(title)
    elif mode == "manual":
        title, _ = await manual_parse_piston_door_title(title)
        return title


class DoorTitle(BaseModel):
    record_category: RecordCategory | None = Field(..., description="The record category of the door")
    component_restrictions: list[str] = Field(..., description="The restrictions on the components of the door")
    door_width: int | None = Field(..., description="the width of the door")
    door_height: int | None = Field(..., description="the height of the door")
    door_depth: int | None = Field(..., description="the depth of the door")
    wiring_placement_restrictions: list[str] = Field(
        ..., description="The restrictions on the wiring placement of the door"
    )
    door_types: list[str] = Field(..., description="The patterns of the door")
    orientation: DoorOrientationName = Field(..., description="The orientation of the door")


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


async def manual_parse_piston_door_title(title: str) -> tuple[DoorTitle, str]:
    """Parses a piston door title into its components."""
    title = title.lower()

    # Define record categories
    record_categories = ["smallest", "fastest", "first"]

    # Check for record category
    record_category = None
    for category in record_categories:
        if title.startswith(category):
            record_category = category.capitalize()
            title = title[len(category) :].strip()
            break

    # Extract door size
    door_size_match = re.search(r"\d+x\d+(x\d+)?", title)
    door_size = (None, None, None)
    if door_size_match:
        door_size_str = door_size_match.group()
        door_size = tuple(map(int, door_size_str.split("x")))
        if len(door_size) == 2:
            door_size = (*door_size, None)
        title = replace_insensitive(title, door_size_str, "").strip()

    # Split the remaining title by known door types
    door_types = []
    for door_type in await get_valid_door_types():
        if door_type.lower() in title.lower():
            door_types.append(door_type)
            title = replace_insensitive(title, door_type, "").strip()

    # Split remaining by orientation
    orientation: DoorOrientationName | None = None
    for orient in DOOR_ORIENTATION_NAMES:
        if orient.lower() in title:
            orientation = orient
            title = replace_insensitive(title, orient, "").strip()
            break
    if orientation is None:
        orientation = "Door"

    # Remaining words are restrictions
    words = title.split()

    component_restrictions = []
    wiring_placement_restrictions = []
    unparsed = []
    for word in words:
        if word.title() in await get_valid_restrictions("component"):
            component_restrictions.append(word.title())
        elif word.title() in await get_valid_restrictions("wiring-placement"):
            wiring_placement_restrictions.append(word.title())
        else:
            unparsed.append(word)

    assert orientation is not None
    return DoorTitle(
        record_category=record_category,
        component_restrictions=component_restrictions,
        door_width=door_size[0],
        door_height=door_size[1],
        door_depth=door_size[2],
        wiring_placement_restrictions=wiring_placement_restrictions,
        door_types=door_types,
        orientation=orientation,
    ), ", ".join(unparsed)


async def ai_parse_piston_door_title(title: str) -> DoorTitle:
    """Parses a piston door title into its components using AI."""
    client = AsyncOpenAI()
    system_prompt = dedent("""
        You are an expert at structured data extraction. You will be given unstructured text from a minecraft piston door name and should convert it into the given structure.
        A build title is in the format of:
        ```
        [Record Category] [component restrictions]+ <door size> [wiring placement restrictions]+ <door type>+ <orientation>
        ```
        
        Non exhaustive list of component restrictions (examples only, users may use other names): 'No Slime Blocks', 'No Observers', 'No Note Blocks', 'No Clocks', 'No Entities', 'No Flying Machines', 'Zomba', 'Zombi', 'Torch and Dust Only', 'Redstone Block Only'
        Non exhaustive list of wiring placement restrictions: 'Super Seamless', 'Full Seamless', 'Semi Seamless', 'Quart Seamless', 'Dentless', 'Full Trapdoor', 'Flush', 'Deluxe', 'Flush Layout', 'Semi Flush', 'Semi Deluxe', 'Semi Wall Hipster', 'Expandable', 'Full Tileable', 'Semi Tileable'
        Non exhaustive list of door types: 'Regular', 'Funnel', 'Asdjke', 'Cave', 'Corner', 'Dual Cave Corner', 'Staircase', 'Gold Play Button', 'Vortex', 'Pitch', 'Bar', 'Vertical', 'Yaw', 'Reversed', 'Inverted', 'Dual', 'Vault', 'Iris', 'Onion', 'Stargate', 'Full Lamp', 'Lamp', 'Hidden Lamp', 'Sissy Bar', 'Checkerboard', 'Windows', 'Redstone Block Center', 'Sand', 'Glass Stripe', 'Center Glass', 'Always On Lamp', 'Circle', 'Triangle', 'Right Triangle', 'Banana', 'Diamond', 'Slab-Shifted', 'Rail', 'Dual Rail', 'Carpet', 'Semi TNT', 'Full TNT'
        
        Examples:
        Title: "Smallest 5 high Dentless triangle cave piston door"
        Parsed: {"record_category": "Smallest", "component_restrictions": [], "door_width": null, "door_height": 5, "door_depth": null, "wiring_placement_restrictions": ["Dentless"], "door_types": ["Triangle", "Cave"], "orientation": "Door"}
        Title: "Zomba 2x2x6 Flush Reverse Pitch Door"
        Parsed: {"record_category": null, "component_restrictions": ["Zomba"], "door_width": 2, "door_height": 2, "door_depth": 6, "wiring_placement_restrictions": ["Flush"], "door_types": ["Reverse", "Pitch"], "orientation": "Door"}
        
        1. Remember that you are allowed to put in names that does not exist in the list, as the list given to you is non exhaustive. Make sure you put in all the information given to you in the title.
        2. If there are names that you don't recognize following the dimensions, use your best judgement to determine whether it is a wiring placement restriction or a door type. If you are unsure, assume it is a door type. (i.e. "3x3 Weird Door" -> the door type is "Weird")
    """)

    completion = await client.beta.chat.completions.parse(
        model="gpt-4o-mini-2024-07-18",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Parse the following door title: {title}"},
        ],
        response_format=DoorTitle,
    )
    return completion.choices[0].message.parsed


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


@alru_cache()
async def get_valid_door_types() -> list[str]:
    """Gets a list of valid door types.

    Returns:
        A list of valid door types.
    """
    db = DatabaseManager()
    valid_door_types_response = await db.table("types").select("name").eq("build_category", "Door").execute()
    return [door_type["name"] for door_type in valid_door_types_response.data]


# --- Unused ---
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
        raise ValueError(
            f"Invalid {type} restrictions. Found {invalid_restrictions} which are not one of the restrictions in the database."
        )
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
    invalid_door_types = [dt for dt in door_types if dt not in await get_valid_door_types()]
    if invalid_door_types:
        raise ValueError(
            f"Invalid door types. Found {invalid_door_types} which are not one of the door types in the database."
        )
    return door_types


def check_is_staff():
    """Check if the user has a staff role, as defined in the server settings."""

    async def predicate(ctx: Context) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()

        server_id = ctx.guild.id
        staff_role_ids = await get_server_setting(server_id=server_id, setting="Staff")
        if staff_role_ids is None:
            return False

        # ctx.guild is None doesn't narrow ctx.author to Member
        if any(ctx.author.get_role(item) is not None for item in staff_role_ids):
            return True
        raise MissingAnyRole(list(staff_role_ids))

    return check(predicate)


def check_is_trusted():
    """Check if the user has a trusted role, as defined in the server settings."""

    async def predicate(ctx: Context) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()

        server_id = ctx.guild.id
        trusted_role_ids = await get_server_setting(server_id=server_id, setting="Trusted")
        if trusted_role_ids is None:
            return False

        # ctx.guild is None doesn't narrow ctx.author to Member
        if any(ctx.author.get_role(item) is not None for item in trusted_role_ids):
            return True
        raise MissingAnyRole(list(trusted_role_ids))

    return check(predicate)


@overload
async def getch(bot: discord.Client, record: MessageRecord | DeleteLogVoteSessionRecord) -> Message: ...


async def getch(bot: discord.Client, record: Mapping[str, Any]) -> Any:
    """Fetch discord objects from database records."""

    try:
        message_adapter = TypeAdapter(MessageRecord)
        message_adapter.validate_python(record)
        message_id = record["message_id"]
        channel_id = record["channel_id"]
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
        channel = cast(GuildMessageable, channel)
        assert isinstance(channel, GuildMessageable), f"{type(channel)=}"
        return await channel.fetch_message(message_id)
    except ValidationError:
        pass

    try:
        message_adapter = TypeAdapter(DeleteLogVoteSessionRecord)
        message_adapter.validate_python(record)
        message_id = record["target_message_id"]
        channel_id = record["target_channel_id"]
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
        channel = cast(GuildMessageable, channel)
        assert isinstance(channel, GuildMessageable), f"{type(channel)=}"
        return await channel.fetch_message(message_id)
    except ValidationError:
        pass

    raise ValueError("Invalid object to fetch.")


async def main():
    await DatabaseManager.setup()
    l = await get_valid_door_types()
    print(l)


if __name__ == "__main__":
    from dotenv import load_dotenv
    import asyncio

    load_dotenv()
    asyncio.run(main())
