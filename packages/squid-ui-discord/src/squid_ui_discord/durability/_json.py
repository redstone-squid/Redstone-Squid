"""Runtime narrowing helpers shared by the durable JSON codecs."""

import math
from collections.abc import Mapping

from squid_ui_discord.durability import MessageRootStateError


def require_object(value: object, description: str) -> dict[str, object]:
    """Narrow an untrusted value to a JSON object with string keys."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        message = f"{description} must be an object with string keys"
        raise MessageRootStateError(message)
    return value


def require_string(raw: Mapping[str, object], key: str, *, description: str = "field") -> str:
    """Read one required string field from an untrusted object."""
    value = raw.get(key)
    if not isinstance(value, str):
        message = f"{description} {key!r} must be a string"
        raise MessageRootStateError(message)
    return value


def require_integer(raw: Mapping[str, object], key: str, *, description: str = "field") -> int:
    """Read one required non-boolean integer field."""
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{description} {key!r} must be an integer"
        raise MessageRootStateError(message)
    return value


def require_number(raw: Mapping[str, object], key: str, *, description: str = "field") -> float:
    """Read one required finite number field."""
    value = raw.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        message = f"{description} {key!r} must be a finite number"
        raise MessageRootStateError(message)
    return float(value)


def optional_string(raw: Mapping[str, object], key: str, *, description: str = "field") -> str | None:
    """Read one optional string field."""
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        message = f"{description} {key!r} must be a string or null"
        raise MessageRootStateError(message)
    return value


def require_boolean(raw: Mapping[str, object], key: str, *, description: str = "field") -> bool:
    """Read one required boolean field."""
    value = raw.get(key)
    if not isinstance(value, bool):
        message = f"{description} {key!r} must be a boolean"
        raise MessageRootStateError(message)
    return value


def require_strings(raw: Mapping[str, object], key: str, *, description: str = "field") -> tuple[str, ...]:
    """Read one required array of strings."""
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        message = f"{description} {key!r} must be an array of strings"
        raise MessageRootStateError(message)
    return tuple(value)


__all__ = [
    "optional_string",
    "require_boolean",
    "require_integer",
    "require_number",
    "require_object",
    "require_string",
    "require_strings",
]
