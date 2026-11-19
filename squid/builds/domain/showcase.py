"""Showcase tags as form answers and edit patches carry them, before they meet a definition."""

import re
from dataclasses import dataclass

from squid.core.errors import JSONValue, ValidationError

_STABLE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class ShowcaseTag:
    """A showcase tag's stable key and its value as entered.

    `value` is the raw text, such as "1.5" or "true", in the tag's canonical unit. Only code
    that has the tag definition can check it, with `coerce_assignment_value`. None means no
    value, which is right for tags that take none.
    """

    key: str
    value: str | None = None

    def __post_init__(self) -> None:
        if _STABLE_KEY.fullmatch(self.key) is None:
            msg = "Showcase tag keys must be stable lowercase identifiers."
            raise ValidationError(msg)

    def to_json(self) -> dict[str, JSONValue]:
        return {"key": self.key} if self.value is None else {"key": self.key, "value": self.value}


def read_showcase_tags(value: JSONValue) -> tuple[ShowcaseTag, ...]:
    """Validate a list of `{"key": ..., "value": ...}` objects with unique keys.

    Raises:
        ValidationError: The list, an item, or a key is malformed, or a key repeats.
    """
    if not isinstance(value, list):
        msg = "Showcase tags must be a list."
        raise ValidationError(msg)
    tags: list[ShowcaseTag] = []
    for item in value:
        if not isinstance(item, dict) or item.keys() - {"key", "value"}:
            msg = "Each showcase tag must be an object with a key and an optional value."
            raise ValidationError(msg)
        key, tag_value = item.get("key"), item.get("value")
        if not isinstance(key, str) or (tag_value is not None and not isinstance(tag_value, str)):
            msg = "A showcase tag's key and value must be text."
            raise ValidationError(msg)
        tags.append(ShowcaseTag(key, tag_value))
    if len({tag.key for tag in tags}) != len(tags):
        msg = "Showcase tag keys must be unique."
        raise ValidationError(msg)
    return tuple(tags)
