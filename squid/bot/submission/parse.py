"""Functions for parsing user input."""

import asyncio
import json
import logging
import re
import typing
from collections.abc import Callable, Iterator, MutableMapping
from io import StringIO
from typing import Any, Literal, Protocol, cast, overload
from xml.etree.ElementTree import Element

from beartype.door import is_bearable, is_subhint  # type: ignore [reportUnknownVariableType]
from markdown import Markdown

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
        msg = f"min_dim must be less than or equal to max_dim. Got {min_dim=} and {max_dim=}."
        raise ValueError(msg)

    inputs_cross = dim_str.split("x")
    inputs_star = dim_str.split("*")
    if min_dim <= len(inputs_cross) <= max_dim:
        inputs = inputs_cross
    elif min_dim <= len(inputs_star) <= max_dim:
        inputs = inputs_star
    else:
        msg = f"Invalid number of dimensions. Expected {min_dim} to {max_dim} dimensions, found {len(inputs_cross)} in {dim_str=} splitting by 'x', and {len(inputs_star)} splitting by '*'."
        raise ValueError(msg)

    dimensions: list[int | None] = []
    for dim in inputs:
        dim = dim.strip()
        if dim == "?":
            dimensions.append(None)
        else:
            try:
                dimensions.append(int(dim))
            except ValueError:
                msg = f"Invalid input. Each dimension must be parsable as an integer, found {inputs}. Parsing failed at '{dim}'"
                raise ValueError(msg)

    # Pad with None
    return tuple(dimensions + [None] * (max_dim - len(dimensions)))


def format_dimensions(dims: tuple[int | None, ...]) -> str:
    """Formats a tuple of dimensions into a string."""
    return " x ".join(str(i) if i is not None else "?" for i in dims)


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
        # direction == "high"
        return -1, int(size), -1

    try:
        return parse_dimensions(dim_str)
    except ValueError:
        msg = "Invalid hallway size. Must be in the format 'width x height [x depth]' or '<width> wide' or '<height> high'"
        raise ValueError(msg)


# Everything is extremely cursed below this line, only read if you dare
type DispatchTuple[T] = tuple[Callable[[T], str], Callable[[str], T]]


def get_formatter_and_parser_for_type[T](attr_type: type[T]) -> DispatchTuple[T]:
    """Get the formatter and parser for a single type.

    Args:
        attr_type: The type to get the formatter and parser for.
    """
    # We abused types so hard here that pyright needs a little help
    formatter: Callable[[T], str] | None = None
    parser: Callable[[str], T] | None = None

    if dispatch := dispatcher.get(attr_type):
        formatter, parser = dispatch
    else:
        for dispatch_hint, dispatch in dispatcher.items():
            if is_subhint(attr_type, dispatch_hint):
                formatter, parser = dispatch
                break
        else:
            if is_subhint(attr_type, list):
                formatter, parser = handle_list(attr_type)  # type: ignore
            elif is_bearable(None, attr_type):
                formatter, parser = handle_optional(attr_type)  # type: ignore
    if formatter is None or parser is None:
        msg = f"No dispatch found for {attr_type}"
        raise RuntimeError(msg)

    dispatcher[attr_type] = formatter, parser
    return formatter, parser


def handle_list[T](outer_type: type[list[T]]) -> DispatchTuple[list[T]]:
    """Generate a formatter and parser for a list type."""
    inner_type = cast(type[T], outer_type.__args__[0])  # type: ignore
    inner_fmt, inner_parser = get_formatter_and_parser_for_type(inner_type)

    def _format(lst: list[T]) -> str:
        return ", ".join(inner_fmt(i) for i in lst)

    def _parse(lst_str: str) -> list[T]:
        return [inner_parser(i) for i in lst_str.split(",")]

    return _format, _parse


def handle_optional[T](outer_type: type[T | type[None]]) -> DispatchTuple[T | None]:
    """Generate a formatter and parser for an Optional type."""
    args = typing.get_args(outer_type)
    if len(args) != 2 or type(None) not in args:
        msg = f"Invalid Optional type: {outer_type}"
        raise ValueError(msg)
    inner_type: type[T] = args[0] if args[0] is not type(None) else args[1]
    inner_fmt, inner_parser = get_formatter_and_parser_for_type(inner_type)

    def _format(x: T | None) -> str:
        if x is None:
            return ""
        return inner_fmt(x)

    def _parse(x: str) -> T | None:
        if not x:
            return None
        return inner_parser(x)

    return _format, _parse


# It is impossible to type this object with anything saner than a Protocol
class DispatchMapping(Protocol):
    def __getitem__[T](self, key: type[T]) -> DispatchTuple[T]: ...
    def __setitem__[T](self, key: type[T], value: DispatchTuple[T]) -> None: ...
    def get[T](self, key: type[T]) -> DispatchTuple[T] | None: ...
    def items(self) -> Iterator[tuple[type, DispatchTuple[Any]]]: ...


dispatcher: DispatchMapping = {  # type: ignore
    str: (str, str),
    int: (str, int),
    tuple[int | None, ...]: (format_dimensions, parse_dimensions),
    MutableMapping: (json.dumps, json.loads),
}


async def main():
    import dotenv

    dotenv.load_dotenv()


if __name__ == "__main__":
    asyncio.run(main())
