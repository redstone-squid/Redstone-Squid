"""Shared escaping and URL validation for HTML renderers."""

from html import escape
from urllib.parse import urlsplit


def attribute(value: object) -> str:
    """Escape one value for a quoted HTML attribute."""
    return escape(str(value), quote=True)


def safe_url(value: str) -> str | None:
    """Return an absolute HTTP(S) URL, rejecting unsafe or relative references."""
    parsed = urlsplit(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


__all__ = ["attribute", "safe_url"]
