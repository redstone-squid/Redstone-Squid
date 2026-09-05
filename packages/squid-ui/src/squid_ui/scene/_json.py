"""Typed validators shared by scene JSON decoders."""

from collections.abc import Callable, Mapping

type JsonObject = Mapping[str, object]
type JsonArray = list[object]


def require_object[ErrorT: Exception](value: object, message: str, error: Callable[[str], ErrorT]) -> JsonObject:
    """Narrow an unknown decoded value to a JSON object or raise ``error``."""
    if not isinstance(value, dict):
        raise error(message)
    narrowed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            key_message = f"{message}; object keys must be strings"
            raise error(key_message)
        narrowed[key] = item
    return narrowed


def require_array[ErrorT: Exception](raw: JsonObject, key: str, error: Callable[[str], ErrorT]) -> JsonArray:
    """Read a required JSON array field."""
    value = raw.get(key)
    if not isinstance(value, list):
        message = f"{key} must be an array"
        raise error(message)
    return value


def require_array_value[ErrorT: Exception](value: object, message: str, error: Callable[[str], ErrorT]) -> JsonArray:
    """Narrow an unknown decoded value to a JSON array."""
    if not isinstance(value, list):
        raise error(message)
    return value


def require_string[ErrorT: Exception](raw: JsonObject, key: str, error: Callable[[str], ErrorT]) -> str:
    """Read a required string field."""
    value = raw.get(key)
    if not isinstance(value, str):
        message = f"{key} must be a string"
        raise error(message)
    return value


def optional_string[ErrorT: Exception](raw: JsonObject, key: str, error: Callable[[str], ErrorT]) -> str | None:
    """Read a nullable string field."""
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        message = f"{key} must be a string or null"
        raise error(message)
    return value


def require_integer[ErrorT: Exception](raw: JsonObject, key: str, error: Callable[[str], ErrorT]) -> int:
    """Read an integer field without accepting booleans as integers."""
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{key} must be an integer"
        raise error(message)
    return value


def optional_integer[ErrorT: Exception](raw: JsonObject, key: str, error: Callable[[str], ErrorT]) -> int | None:
    """Read a nullable integer field without accepting booleans."""
    value = raw.get(key)
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        message = f"{key} must be an integer or null"
        raise error(message)
    return value


def require_boolean[ErrorT: Exception](
    raw: JsonObject,
    key: str,
    error: Callable[[str], ErrorT],
    *,
    default: bool | None = None,
) -> bool:
    """Read a boolean field, optionally supplying a missing-field default."""
    value = raw.get(key, default)
    if not isinstance(value, bool):
        message = f"{key} must be a boolean"
        raise error(message)
    return value


def require_string_array[ErrorT: Exception](raw: JsonObject, key: str, error: Callable[[str], ErrorT]) -> list[str]:
    """Read an array containing only strings."""
    value = raw.get(key)
    if not isinstance(value, list):
        message = f"{key} must be an array of strings"
        raise error(message)
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            message = f"{key} must be an array of strings"
            raise error(message)
        strings.append(item)
    return strings


__all__ = [
    "JsonArray",
    "JsonObject",
    "optional_integer",
    "optional_string",
    "require_array",
    "require_array_value",
    "require_boolean",
    "require_integer",
    "require_object",
    "require_string",
    "require_string_array",
]
