"""Shared escaping and URL validation for HTML renderers."""

from html import escape
from urllib.parse import urlsplit


def attribute(value: object) -> str:
    """Escape one value for a quoted HTML attribute."""
    return escape(str(value), quote=True)


def safe_url(value: str) -> str | None:
    """Accept references with an HTTP(S) scheme and a nonempty network location.

    This filters schemes; it does not authorize a destination or fetch its content.
    Malformed URL syntax may raise ValueError from urlsplit.
    """
    parsed = urlsplit(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


__all__ = ["attribute", "safe_url"]
