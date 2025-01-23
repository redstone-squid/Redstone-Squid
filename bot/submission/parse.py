import asyncio
import logging
import re
from io import StringIO
from typing import Literal, overload
from xml.etree.ElementTree import Element

from markdown import Markdown
from postgrest.base_request_builder import APIResponse

from database import DatabaseManager
from database.schema import RestrictionRecord, TypeRecord

logger = logging.getLogger(__name__)


# See https://stackoverflow.com/questions/761824/python-how-to-convert-markdown-formatted-text-to-text
def _unmark_element(element: Element, stream: StringIO | None = None):
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
    """Removes Markdown formatting from a string."""
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
    """Gets a list of valid restrictions for a given type. The restrictions are returned in the original case.

    Args:
        type: The type of restriction. Either "component", "wiring_placement" or "miscellaneous"

    Returns:
        A list of valid restrictions for the given type.
    """
    db = DatabaseManager()
    valid_restrictions_response: APIResponse[RestrictionRecord] = (
        await db.table("restrictions").select("name").eq("type", type).execute()
    )
    return [restriction["name"] for restriction in valid_restrictions_response.data]


async def get_valid_door_types() -> list[str]:
    """Gets a list of valid door types. The door types are returned in the original case.

    Returns:
        A list of valid door types.
    """
    db = DatabaseManager()
    valid_door_types_response: APIResponse[TypeRecord] = (
        await db.table("types").select("name").eq("build_category", "Door").execute()
    )
    return [door_type["name"] for door_type in valid_door_types_response.data]


async def validate_restrictions(
    restrictions: list[str], type: Literal["component", "wiring-placement", "miscellaneous"]
) -> tuple[list[str], list[str]]:
    """Validates a list of restrictions for a given type.

    Args:
        restrictions: The list of restrictions to validate
        type: The type of restriction. Either "component", "wiring_placement" or "miscellaneous"

    Returns:
        (valid_restrictions, invalid_restrictions)
    """
    all_valid_restrictions = [r.lower() for r in await get_valid_restrictions(type)]

    valid_restrictions = [r for r in restrictions if r.lower() in all_valid_restrictions]
    invalid_restrictions = [r for r in restrictions if r not in all_valid_restrictions]
    return valid_restrictions, invalid_restrictions


async def validate_door_types(door_types: list[str]) -> tuple[list[str], list[str]]:
    """Validates a list of door types.

    Args:
        door_types: The list of door types to validate

    Returns:
        (valid_door_types, invalid_door_types)
    """
    all_valid_door_types = [t.lower() for t in await get_valid_door_types()]

    valid_door_types = [t for t in door_types if t.lower() in all_valid_door_types]
    invalid_door_types = [t for t in door_types if t.lower() not in all_valid_door_types]
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


@overload
def parse_dimensions(dim_str: str) -> tuple[int | None, int | None, int | None]: ...


@overload
def parse_dimensions(
    dim_str: str, *, min_dim: int, max_dim: Literal[3]
) -> tuple[int | None, int | None, int | None]: ...


def parse_dimensions(dim_str: str, *, min_dim: int = 2, max_dim: int = 3) -> tuple[int | None, ...]:
    """Parses a string representing dimensions.

    For example, '5x5' or '5x5x5'. Both 'x' and '*' are valid separators. '?' is allowed as a placeholder for a dimension.

    Args:
        dim_str: The string to parse
        min_dim: The minimum number of dimensions
        max_dim: The maximum number of dimensions

    Returns:
        A list of the dimensions, the length of the list will match `max_dim`. If there are fewer dimensions than `max_dim`, the rest will be `None`.

    Raises:
        ValueError: If the number of dimensions is not between `min_dim` and `max_dim`, or the string is not parsable.
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

    dimensions: list[int | None] = []
    for dim in inputs:
        dim = dim.strip()
        if dim == "?":
            dimensions.append(None)
        else:
            try:
                dimensions.append(int(dim))
            except ValueError:
                raise ValueError(
                    f"Invalid input. Each dimension must be parsable as an integer, found {inputs}. Parsing failed at '{dim}'"
                )

    # Pad with None
    return tuple(dimensions + [None] * (max_dim - len(dimensions)))


def parse_hallway_dimensions(dim_str: str) -> tuple[int | None, int | None, int | None]:
    """Parses a string representing the door's <size>, which essentially is the hallway's dimensions.

    None is used to represent a dimension that is not given. The value -1 is used to represent a dimension that is not applicable.

    Examples:
        "5x5x5" -> (5, 5, 5)
        "5x5" -> (5, 5, None)
        "5 wide" -> (5, -1, -1)
        "5 high" -> (-1, 5, -1)

    References:
        https://docs.google.com/document/d/1kDNXIvQ8uAMU5qRFXIk6nLxbVliIjcMu1MjHjLJrRH4/edit

    Returns:
        A tuple of the dimensions (width, height, depth).
    """
    if match := re.match(r"^(\d+)\s*(wide|high)$", dim_str):
        size, direction = match.groups()
        if direction == "wide":
            return int(size), -1, -1
        else:  # direction == "high"
            return -1, int(size), -1

    try:
        return parse_dimensions(dim_str)
    except ValueError:
        raise ValueError(
            "Invalid hallway size. Must be in the format 'width x height [x depth]' or '<width> wide' or '<height> high'"
        )


async def main():
    import dotenv

    dotenv.load_dotenv()


if __name__ == "__main__":
    asyncio.run(main())
