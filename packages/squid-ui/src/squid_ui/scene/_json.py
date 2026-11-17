"""Typed field readers shared by scene JSON decoders."""

from collections.abc import Callable, Mapping

type JsonObject = Mapping[str, object]
type JsonArray = list[object]


class JsonReader:
    """Read decoded JSON fields using the owning codec's error type."""

    def __init__(self, error: Callable[[str], Exception]) -> None:
        self.error = error

    def require_object(self, value: object, *, name: str | None = None) -> JsonObject:
        """Copy a decoded dict after checking string keys."""
        message = "expected an object" if name is None else f"{name} must be an object"
        if not isinstance(value, dict):
            raise self.error(message)
        narrowed: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = f"{message}; object keys must be strings"
                raise self.error(msg)
            narrowed[key] = item
        return narrowed

    def array(self, raw: JsonObject, key: str) -> JsonArray:
        value = raw.get(key)
        if not isinstance(value, list):
            msg = f"{key} must be an array"
            raise self.error(msg)
        return value

    def array_value(self, value: object, message: str) -> JsonArray:
        if not isinstance(value, list):
            raise self.error(message)
        return value

    def string(self, raw: JsonObject, key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str):
            msg = f"{key} must be a string"
            raise self.error(msg)
        return value

    def optional_string(self, raw: JsonObject, key: str) -> str | None:
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            msg = f"{key} must be a string or null"
            raise self.error(msg)
        return value

    def integer(self, raw: JsonObject, key: str) -> int:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            msg = f"{key} must be an integer"
            raise self.error(msg)
        return value

    def optional_integer(self, raw: JsonObject, key: str) -> int | None:
        value = raw.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            msg = f"{key} must be an integer or null"
            raise self.error(msg)
        return value

    def boolean(self, raw: JsonObject, key: str, *, default: bool | None = None) -> bool:
        value = raw.get(key, default)
        if not isinstance(value, bool):
            msg = f"{key} must be a boolean"
            raise self.error(msg)
        return value

    def string_array(self, raw: JsonObject, key: str) -> list[str]:
        value = raw.get(key)
        if not isinstance(value, list):
            msg = f"{key} must be an array of strings"
            raise self.error(msg)
        strings: list[str] = []
        for item in value:
            if not isinstance(item, str):
                msg = f"{key} must be an array of strings"
                raise self.error(msg)
            strings.append(item)
        return strings


__all__ = ["JsonArray", "JsonObject", "JsonReader"]
