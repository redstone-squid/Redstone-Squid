"""Shared parsing for submission text inputs across commands and forms."""

from collections.abc import Iterable
from urllib.parse import urlsplit


def split_values(value: str) -> list[str]:
    """Split a comma-separated value, trimming entries and discarding empty ones."""
    return [item for entry in value.split(",") if (item := entry.strip())]


def optional_text(value: str) -> str | None:
    """Trim optional text and normalize an empty value to ``None``."""
    return value.strip() or None


def invalid_web_urls(values: Iterable[str]) -> tuple[str, ...]:
    """Return values that are not absolute HTTP(S) URLs, preserving input order."""
    invalid: list[str] = []
    for value in values:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            invalid.append(value)
    return tuple(invalid)


def format_invalid_values(values: Iterable[str], *, maximum: int = 500) -> str:
    """Render offending values deterministically inside a Discord-safe character budget."""
    if maximum < 2:
        msg = "Invalid-value display budget must leave room for an ellipsis."
        raise ValueError(msg)
    rendered = ", ".join(f"`{value.replace('`', "'")}`" for value in values)
    return rendered if len(rendered) <= maximum else f"{rendered[: maximum - 1].rstrip()}…"


def parse_web_urls(value: str) -> list[str]:
    """Parse comma-separated HTTP(S) URLs or name every invalid submitted value."""
    urls = split_values(value)
    if invalid := invalid_web_urls(urls):
        displayed = format_invalid_values(invalid)
        message = f"Use complete https:// or http:// links. Invalid: {displayed}"
        raise ValueError(message)
    return urls
