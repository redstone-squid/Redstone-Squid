import asyncio
import logging
import os
import re
from io import StringIO
from textwrap import dedent
from typing import Literal, Any
from xml.etree.ElementTree import Element

from async_lru import alru_cache
from markdown import Markdown
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

from database import DatabaseManager
from database.builds import Build
from database.schema import RecordCategory, DoorOrientationName, DOOR_ORIENTATION_NAMES
from database.utils import get_version_string

logger = logging.getLogger(__name__)


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


async def get_valid_restrictions(type: Literal["component", "wiring-placement", "miscellaneous"]) -> list[str]:
    """Gets a list of valid restrictions for a given type.

    Args:
        type: The type of restriction. Either "component", "wiring_placement" or "miscellaneous"

    Returns:
        A list of valid restrictions for the given type.
    """
    db = DatabaseManager()
    valid_restrictions_response = await db.table("restrictions").select("name").eq("type", type).execute()
    return [restriction["name"] for restriction in valid_restrictions_response.data]


async def get_valid_door_types() -> list[str]:
    """Gets a list of valid door types.

    Returns:
        A list of valid door types.
    """
    db = DatabaseManager()
    valid_door_types_response = await db.table("types").select("name").eq("build_category", "Door").execute()
    return [door_type["name"] for door_type in valid_door_types_response.data]


async def validate_restrictions(restrictions: list[str], type: Literal["component", "wiring-placement", "miscellaneous"]) -> tuple[list[str], list[str]]:
    """Validates a list of restrictions for a given type.

    Args:
        restrictions: The list of restrictions to validate
        type: The type of restriction. Either "component", "wiring_placement" or "miscellaneous"

    Returns:
        (valid_restrictions, invalid_restrictions)
    """
    all_valid_restrictions = await get_valid_restrictions(type)

    valid_restrictions = [r for r in restrictions if r in all_valid_restrictions]
    invalid_restrictions = [r for r in restrictions if r not in all_valid_restrictions]
    return valid_restrictions, invalid_restrictions

async def validate_door_types(door_types: list[str]) -> tuple[list[str], list[str]]:
    """Validates a list of door types.

    Args:
        door_types: The list of door types to validate

    Returns:
        (valid_door_types, invalid_door_types)
    """
    all_valid_door_types = await get_valid_door_types()

    valid_door_types = [r for r in door_types if r in all_valid_door_types]
    invalid_door_types = [r for r in door_types if r not in all_valid_door_types]
    return valid_door_types, invalid_door_types

def parse_time_string(time_string: str | None) -> int | None:
    """Parses a time string into an integer.

    Args:
        time_string: The time string to parse.

    Returns:
        The time in ticks.
    """
    if time_string is None:
        return None
    time_string = time_string.replace("s", "").replace("~", "").strip()
    try:
        return int(float(time_string) * 20)
    except ValueError:
        return None

async def parse_build(message: str) -> Build | None:
    """Parses a build from a message using AI."""
    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    with open(f"{__file__}/../prompt.txt", "r", encoding="utf-8") as f:
        prompt = f.read()
    completion = await client.beta.chat.completions.parse(
        model="deepseek/deepseek-chat",
        messages=[
            {"role": "user", "content": prompt.format(message=message)},
        ],
    )
    output = completion.choices[0].message.content

    logger.debug(f"AI Output: {output}")

    if output is None:
        return None

    # Step 1: Extract content between <target> and </target>
    match = re.search(r'<target>(.*?)</target>', output, re.DOTALL)
    if not match:
        return None

    content = match.group(1).strip()

    # Step 2: Split content into lines and parse key-value pairs
    variables: dict[str, str | None] = {}
    for line in content.split('\n'):
        # Skip empty lines
        if not line.strip():
            continue
        # Split only on the first ':'
        if ':' not in line:
            print(f"Skipping malformed line: {line}")
            continue
        key, value = line.split(':', 1)
        key = key.strip()
        value = value.strip()
        if value.lower() in ["none", "null", "unknown"]:
            value = None

        variables[key] = value

    # Step 3: Validate and convert variables
    acceptable_keys = [
        "record_category",
        "component_restriction",
        "wiring_placement_restrictions",
        "miscellaneous_restrictions",
        "piston_door_type",
        "door_orientation",
        "door_width",
        "door_height",
        "door_depth",
        "build_width",
        "build_height",
        "build_depth",
        "opening_time",
        "closing_time",
        "creators",
        "version",
        "image",
        "author_note"
    ]

    # All keys must be present
    if not all(key in variables for key in acceptable_keys):
        logging.debug("Missing keys in AI output variables")
        return

    build = Build()
    build.ai_generated = True
    build.record_category = variables["record_category"]
    build.information["unknown_restrictions"] = {}
    if variables["component_restriction"] is not None:
        comps = await validate_restrictions(variables["component_restriction"].split(", "), "component")
        build.component_restrictions = comps[0]
        build.information["unknown_restrictions"]["component_restrictions"] = comps[1]
    if variables["wiring_placement_restrictions"] is not None:
        wirings = await validate_restrictions(variables["wiring_placement_restrictions"].split(", "), "wiring-placement")
        build.wiring_placement_restrictions = wirings[0]
        build.information["unknown_restrictions"]["wiring_placement_restrictions"] = wirings[1]
    if variables["miscellaneous_restrictions"] is not None:
        miscs = await validate_restrictions(variables["miscellaneous_restrictions"].split(", "), "miscellaneous")
        build.miscellaneous_restrictions = miscs[0]
        build.information["unknown_restrictions"]["miscellaneous_restrictions"] = miscs[1]
    if variables["piston_door_type"] is not None:
        door_types = await validate_door_types(variables["piston_door_type"].split(", "))
        build.door_type = door_types[0]
        build.information["unknown_patterns"] = door_types[1]
    orientation = variables["door_orientation"]
    if orientation == "Normal":
        build.door_orientation_type = "Door"
    else:
        build.door_orientation_type = orientation or "Door"
    build.door_width = int(variables["door_width"]) if variables["door_width"] else None
    build.door_height = int(variables["door_height"]) if variables["door_height"] else None
    build.door_depth = int(variables["door_depth"]) if variables["door_depth"] else None
    build.width = int(variables["build_width"]) if variables["build_width"] else None
    build.height = int(variables["build_height"]) if variables["build_height"] else None
    build.depth = int(variables["build_depth"]) if variables["build_depth"] else None
    build.normal_opening_time = parse_time_string(variables["opening_time"])
    build.normal_closing_time = parse_time_string(variables["closing_time"])
    build.creators_ign = variables["creators"].split(", ") if variables["creators"] else []
    build.version_spec = variables["version"] or DatabaseManager.get_newest_version(edition="Java")
    build.versions = DatabaseManager.filter_versions(build.version_spec)
    build.image_urls = variables["image"].split(", ") if variables["image"] else []
    if variables["author_note"] is not None:
        build.information["user"] = variables["author_note"].replace("\\n", "\n")
    return build


async def main():
    import dotenv
    dotenv.load_dotenv()
    await DatabaseManager.setup()
    build = await parse_build("https://imgur.com/a/ipYjpMj\n\nNot the best, but my first ever RBO\n\n585 Blocks 3x3 Corner Door\n\nSubtract 0.2 seconds from closing/opening time because of the 2 reps used for a good activation point, otherwise its almost impossible to get back from activation point to see door close/open\n\n0.9s Open\n1.2 Close\n(Creeper's timing measurements)\n\nAlso this is tied fastest with || @Cwee957 and @Ashley || so far\n\nSpecial thanks to Toppish for doing the last retraction of the DPE")
    print(build)


if __name__ == "__main__":
    asyncio.run(main())
