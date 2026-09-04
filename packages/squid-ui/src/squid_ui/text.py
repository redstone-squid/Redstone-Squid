"""Resolved text values and safe Discord Markdown interpolation."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from string.templatelib import Interpolation, Template
from typing import Any, Protocol


class Markup(StrEnum):
    """How a renderer should interpret resolved text."""

    PLAIN = "plain"
    """Literal; `discord_text` escapes every Markdown character before Discord sees it."""
    DISCORD_MARKDOWN = "discord-markdown"
    """Already Discord Markdown; passed through unchanged."""


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
    def content(self) -> str:
        """The text, escaped or not according to `markup`."""
        ...

    @property
    def markup(self) -> Markup:
        """What `content` is written in, which decides whether `discord_text` escapes it."""
        ...


@dataclass(frozen=True, slots=True)
class RawMarkdown:
    """An interpolation value `md` and `Message` insert unescaped; the only way dynamic text becomes markup."""

    content: str


def _identity(message: str) -> str:
    return message


@dataclass(frozen=True, slots=True)
class Localization:
    """One negotiated locale and the catalogue used to resolve its messages."""

    locale: str | None = None
    gettext: Callable[[str], str] = _identity
    """Template to translated template; the identity by default."""
    ngettext: Callable[[str, str, int], str] | None = None
    """`(singular, plural, count)` to template; `None` picks `singular` only when `count == 1`."""


NEUTRAL = Localization()

_CURRENT_LOCALIZATION: ContextVar[Localization] = ContextVar("squid_ui_localization", default=NEUTRAL)


def current_localization() -> Localization:
    """Return the localization bound to the current execution context."""
    return _CURRENT_LOCALIZATION.get()


@contextmanager
def localization_scope(localization: Localization) -> Iterator[None]:
    """Bind one localization until the surrounding context exits."""
    token = _CURRENT_LOCALIZATION.set(localization)
    try:
        yield
    finally:
        _CURRENT_LOCALIZATION.reset(token)


@dataclass(frozen=True, slots=True)
class Message:
    """Translatable text deferred until a render has a localization."""

    template: str
    """The `gettext` key, in `str.format` syntax."""
    params: Mapping[str, object] = field(default_factory=dict)
    """Formatted into the translated template, each escaped unless `RawMarkdown`; a nested `Message` resolves first."""
    markup: Markup = Markup.DISCORD_MARKDOWN
    plural: str | None = None
    """Plural form for `ngettext`; requires an `int` under `params["count"]`."""


type TextLike = str | ResolvedText | Message


def raw_md(value: object) -> RawMarkdown:
    """Mark one interpolation as trusted Discord Markdown."""
    return RawMarkdown(str(value))


def plain(value: object) -> ResolvedText:
    """Literal text a renderer must not interpret as Markdown.

    A `datetime` or `Timestamp` becomes its ISO form; raises `ValueError` when it is naive.
    """
    temporal = _temporal_value(value, Markup.PLAIN)
    return ResolvedText(str(value) if temporal is None else temporal, Markup.PLAIN)


def md(value: str | Template, /, **values: object) -> ResolvedText:
    """Resolve trusted Markdown with escaped dynamic interpolations.

    Bare strings are the trusted template markup. Dynamic content is supplied either by a
    Python 3.14 template string or by named values for translated format strings; each value is
    Markdown-escaped and has its `@` neutralized unless it is a `RawMarkdown`, and an aware
    `datetime` becomes a Discord timestamp. Raises `TypeError` for a template string given
    `values`, `ValueError` for a format string its values do not satisfy or a naive `datetime`.
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
    """Resolve author text against a render-time localization.

    A `ResolvedText` is returned as is and a `str` is taken as trusted Discord Markdown; only a
    `Message` is translated. Raises `ValueError` for a plural `Message` without an integer
    `count` param, or a translated template its params do not satisfy.
    """
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
    """The string Discord receives: `PLAIN` content with every Markdown character escaped, Markdown as is."""
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
    escaped = {key: _SafeFormatValue(value, markup) for key, value in values.items()}
    try:
        return template.format_map(escaped)
    except (KeyError, ValueError) as error:
        message = f"invalid Markdown format template: {error}"
        raise ValueError(message) from error


@dataclass(frozen=True, slots=True)
class _SafeFormatValue:
    value: object
    markup: Markup

    def __str__(self) -> str:
        return _safe_value(self.value, self.markup)

    def __repr__(self) -> str:
        return _safe_value(repr(self.value), self.markup)

    def __format__(self, format_spec: str) -> str:
        if not format_spec:
            return _safe_value(self.value, self.markup)
        if isinstance(self.value, RawMarkdown):
            return format(self.value.content, format_spec)
        return _safe_value(format(self.value, format_spec), self.markup)


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
    "current_localization",
    "discord_text",
    "localization_scope",
    "md",
    "plain",
    "raw_md",
    "resolve_text",
]
