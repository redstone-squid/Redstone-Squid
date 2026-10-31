"""Resolved text values and safe Discord Markdown interpolation."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from string.templatelib import Interpolation, Template
from typing import Any


class TextDialect(StrEnum):
    """How a renderer should interpret resolved text."""

    PLAIN = "plain"
    DISCORD_MARKDOWN = "discord-markdown"


@dataclass(frozen=True, slots=True)
class ResolvedText:
    """Text after interpolation and translation, before target planning."""

    content: str
    dialect: TextDialect = TextDialect.DISCORD_MARKDOWN


@dataclass(frozen=True, slots=True)
class RawMarkdown:
    """An explicitly trusted Markdown interpolation value."""

    content: str


type TextLike = str | ResolvedText


def raw_md(value: object) -> RawMarkdown:
    """Mark one interpolation as trusted Discord Markdown."""
    return RawMarkdown(str(value))


def plain(value: object) -> ResolvedText:
    """Create literal text which renderers must not interpret as Markdown."""
    return ResolvedText(str(value), TextDialect.PLAIN)


def md(value: str | Template, /, **values: object) -> ResolvedText:
    """Resolve trusted Markdown with escaped dynamic interpolations.

    Bare strings are the trusted template dialect. Dynamic content is supplied either by a
    Python 3.14 template string or by named values for translated format strings.
    """
    if isinstance(value, Template):
        if values:
            message = "template strings already contain their interpolation values"
            raise TypeError(message)
        return ResolvedText(_resolve_template(value))
    if not values:
        return ResolvedText(value)
    return ResolvedText(_resolve_named(value, values))


def resolve_text(value: TextLike) -> ResolvedText:
    """Normalize author text; bare strings are trusted Discord Markdown."""
    return value if isinstance(value, ResolvedText) else ResolvedText(value)


def discord_text(value: ResolvedText) -> str:
    """Render resolved text into Discord's Markdown input dialect."""
    if value.dialect is TextDialect.DISCORD_MARKDOWN:
        return value.content
    return _escape_markdown(value.content)


def _resolve_template(template: Template) -> str:
    parts: list[str] = []
    for string, interpolation in zip(template.strings, template.interpolations, strict=False):
        parts.append(string)
        parts.append(_interpolation(interpolation))
    parts.append(template.strings[-1])
    return "".join(parts)


def _resolve_named(template: str, values: Mapping[str, object]) -> str:
    escaped = {key: _safe_value(value) for key, value in values.items()}
    try:
        return template.format_map(escaped)
    except (KeyError, ValueError) as error:
        message = f"invalid Markdown format template: {error}"
        raise ValueError(message) from error


def _interpolation(interpolation: Interpolation) -> str:
    value: Any = interpolation.value
    if interpolation.conversion == "r":
        value = repr(value)
    elif interpolation.conversion == "s":
        value = str(value)
    elif interpolation.conversion == "a":
        value = ascii(value)
    if interpolation.format_spec:
        value = format(value, interpolation.format_spec)
    return _safe_value(value)


def _safe_value(value: object) -> str:
    if isinstance(value, RawMarkdown):
        return value.content
    return _neutralize_mentions(_escape_markdown(str(value)))


def _escape_markdown(value: str) -> str:
    escaped: list[str] = []
    for character in value:
        if character in "\\`*_{}[]()<>#+-.!|~":
            escaped.append("\\")
        escaped.append(character)
    return "".join(escaped)


def _neutralize_mentions(value: str) -> str:
    return value.replace("@", "@\u200b")
