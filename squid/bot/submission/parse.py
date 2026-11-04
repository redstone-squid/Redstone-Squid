"""Functions for parsing user input."""

import re
from typing import Literal, overload


@overload
def parse_dimensions(dim_str: str) -> tuple[int | None, int | None, int | None]: ...


@overload
def parse_dimensions(
    dim_str: str, *, min_dim: int, max_dim: Literal[3]
) -> tuple[int | None, int | None, int | None]: ...


def parse_dimensions(dim_str: str, *, min_dim: int = 2, max_dim: int = 3) -> tuple[int | None, ...]:
    """Parse integer dimensions separated by lowercase x or by *, padding with None.

    Accept between min_dim and max_dim entries, strip surrounding whitespace, and
    use ? for unknown values. The returned tuple always has max_dim entries.
    Separators cannot be mixed; positivity is left to domain validation.

    For example, "5 x ?" becomes (5, None, None) with the default bounds.

    Raises:
        ValueError: Bounds are reversed, the entry count is outside them, or an entry
            is neither an integer nor ?.
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
            except ValueError as err:
                msg = f"Invalid input. Each dimension must be parsable as an integer, found {inputs}. Parsing failed at '{dim}'"
                raise ValueError(msg) from err

    return tuple(dimensions + [None] * (max_dim - len(dimensions)))


def format_dimensions(dims: tuple[int | None, ...]) -> str:
    """Join dimensions with x, displaying None as ?."""
    return " x ".join(str(i) if i is not None else "?" for i in dims)


def parse_hallway_dimensions(dim_str: str) -> tuple[int | None, int | None, int | None]:
    """Parse a door opening as width, height, and depth.

    None means unknown; -1 means not applicable for a width-only or height-only opening.

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
        return -1, int(size), -1

    try:
        return parse_dimensions(dim_str)
    except ValueError as err:
        msg = "Invalid hallway size. Must be in the format 'width x height [x depth]' or '<width> wide' or '<height> high'"
        raise ValueError(msg) from err
