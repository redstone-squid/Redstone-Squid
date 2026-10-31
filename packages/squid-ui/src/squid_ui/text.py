"""Resolved text values and safe Discord Markdown interpolation."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from string.templatelib import Interpolation, Template
from typing import Any, Protocol


class Markup(StrEnum):
    """How a renderer should interpret resolved text."""

    PLAIN = "plain"
    DISCORD_MARKDOWN = "discord-markdown"


@dataclass(frozen=True, slots=True)
class ResolvedText:
    """Text after interpolation and translation, before target planning."""

    content: str
    markup: Markup = Markup.DISCORD_MARKDOWN


class MarkupText(Protocol):
    """Resolved content plus the markup it is written in.

    `ResolvedText` and `scene.Text` are the same two fields at two layers, and `scene`
    imports this module, so the seam between them is structural rather than a union.
    Read-only members, because both sides are frozen.
    """

    @property
    def content(self) -> str: ...

    @property
    def markup(self) -> Markup: ...


@dataclass(frozen=True, slots=True)
class RawMarkdown:
    """An explicitly trusted Markdown interpolation value."""

    content: str


def _identity(message: str) -> str:
    return message


@dataclass(frozen=True, slots=True)
class Localization:
    """One negotiated locale and the catalogue used to resolve its messages."""

    locale: str | None = None
    gettext: Callable[[str], str] = _identity
    ngettext: Callable[[str, str, int], str] | None = None


NEUTRAL = Localization()


@dataclass(frozen=True, slots=True)
class Message:
    """Translatable text deferred until a render has a localization."""

    template: str
    params: Mapping[str, object] = field(default_factory=dict)
    markup: Markup = Markup.DISCORD_MARKDOWN
    plural: str | None = None


type TextLike = str | ResolvedText | Message


def raw_md(value: object) -> RawMarkdown:
    """Mark one interpolation as trusted Discord Markdown."""
    return RawMarkdown(str(value))


def plain(value: object) -> ResolvedText:
    """Create literal text which renderers must not interpret as Markdown."""
    temporal = _temporal_value(value, Markup.PLAIN)
    return ResolvedText(str(value) if temporal is None else temporal, Markup.PLAIN)


def md(value: str | Template, /, **values: object) -> ResolvedText:
    """Resolve trusted Markdown with escaped dynamic interpolations.

    Bare strings are the trusted template markup. Dynamic content is supplied either by a
    Python 3.14 template string or by named values for translated format strings.
    """
    if isinstance(value, Template):
        if values:
            message = "template strings already contain their interpolation values"
            raise TypeError(message)
        return ResolvedText(_resolve_template(value, Markup.DISCORD_MARKDOWN))
    if not values:
        return ResolvedText(value)
    return ResolvedText(_resolve_named(value, values, Markup.DISCORD_MARKDOWN))


def resolve_text(value: TextLike, localization: Localization) -> ResolvedText:
    """Resolve author text against a required render-time localization."""
    if isinstance(value, ResolvedText):
        return value
    if isinstance(value, str):
        return ResolvedText(value)
    template = localization.gettext(value.template)
    if value.plural is not None:
        count = value.params.get("count")
        if not isinstance(count, int):
            message = "plural messages require an integer 'count' parameter"
            raise ValueError(message)
        if localization.ngettext is not None:
            template = localization.ngettext(value.template, value.plural, count)
        else:
            template = value.template if count == 1 else value.plural
    params = {
        key: raw_md(resolve_text(param, localization).content) if isinstance(param, Message | ResolvedText) else param
        for key, param in value.params.items()
    }
    content = _resolve_named(template, params, value.markup) if params else template
    return ResolvedText(content, value.markup)


def discord_text(value: MarkupText) -> str:
    """Render resolved text into Discord's Markdown input markup."""
    if value.markup is Markup.DISCORD_MARKDOWN:
        return value.content
    return _escape_markdown(value.content)


def _resolve_template(template: Template, markup: Markup) -> str:
    parts: list[str] = []
    for string, interpolation in zip(template.strings, template.interpolations, strict=False):
        parts.append(string)
        parts.append(_interpolation(interpolation, markup))
    parts.append(template.strings[-1])
    return "".join(parts)


def _resolve_named(template: str, values: Mapping[str, object], markup: Markup) -> str:
    escaped = {key: _safe_value(value, markup) for key, value in values.items()}
    try:
        return template.format_map(escaped)
    except (KeyError, ValueError) as error:
        message = f"invalid Markdown format template: {error}"
        raise ValueError(message) from error


def _interpolation(interpolation: Interpolation, markup: Markup) -> str:
    value: Any = interpolation.value
    if interpolation.conversion == "r":
        value = repr(value)
    elif interpolation.conversion == "s":
        value = str(value)
    elif interpolation.conversion == "a":
        value = ascii(value)
    if interpolation.format_spec:
        value = format(value, interpolation.format_spec)
    return _safe_value(value, markup)


def _safe_value(value: object, markup: Markup) -> str:
    if isinstance(value, RawMarkdown):
        return value.content
    temporal = _temporal_value(value, markup)
    if temporal is not None:
        return temporal
    return _neutralize_mentions(_escape_markdown(str(value)))


def _temporal_value(value: object, markup: Markup) -> str | None:
    from squid_ui.semantic import Timestamp

    if isinstance(value, Timestamp):
        instant = value.instant
        style = value.style.value
    elif isinstance(value, datetime):
        instant = value
        style = "f"
    else:
        return None
    if instant.tzinfo is None or instant.utcoffset() is None:
        message = "timestamp interpolation requires an aware datetime"
        raise ValueError(message)
    if markup is Markup.PLAIN:
        return instant.isoformat()
    return f"<t:{int(instant.timestamp())}:{style}>"


def _escape_markdown(value: str) -> str:
    escaped: list[str] = []
    for character in value:
        if character in "\\`*_{}[]()<>#+-.!|~":
            escaped.append("\\")
        escaped.append(character)
    return "".join(escaped)


def _neutralize_mentions(value: str) -> str:
    return value.replace("@", "@\u200b")


__all__ = [
    "NEUTRAL",
    "Localization",
    "Markup",
    "MarkupText",
    "Message",
    "RawMarkdown",
    "ResolvedText",
    "TextLike",
    "discord_text",
    "md",
    "plain",
    "raw_md",
    "resolve_text",
]
