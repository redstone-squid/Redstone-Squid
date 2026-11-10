"""Prepare authored text for localization and target rendering.

Bare strings are trusted Discord Markdown. Use plain for literal text, or md to keep
template formatting while escaping interpolated values. Message defers translation
until the render's Localization is known; resolution produces ResolvedText carrying
both content and its markup type for the planner and renderer.
"""

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from string.templatelib import Interpolation, Template
from typing import Any, Protocol, overload, override


class Markup(StrEnum):
    """How a renderer should interpret resolved text."""

    PLAIN = "plain"
    DISCORD_MARKDOWN = "discord-markdown"


@dataclass(frozen=True, slots=True)
class ResolvedText:
    """Text after interpolation and translation, before target planning.

    Concatenating with `+` follows the same rules as `Message`. Joining resolved text with a
    bare string or more resolved text gives resolved Markdown; joining it with a Message gives
    a Message.
    """

    content: str
    markup: Markup = Markup.DISCORD_MARKDOWN

    @overload
    def __add__(self, other: str | ResolvedText) -> ResolvedText: ...
    @overload
    def __add__(self, other: Message) -> Message: ...
    def __add__(self, other: TextLike) -> ResolvedText | Message:
        if isinstance(other, Message):
            return Message.join("", (self, other))
        return ResolvedText(discord_text(self) + discord_text(_as_resolved(other)))

    def __radd__(self, other: str) -> ResolvedText:
        return ResolvedText(other + discord_text(self))

    def resolve(self) -> str:
        """Return this text as Discord Markdown."""
        return discord_text(self)


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
    """Translatable text deferred until a render has a localization.

    A Message holds a catalogue key and its values, not text, so it cannot be formatted or
    measured as a string. Pass it to a renderer, interpolate it into another Message, or
    combine it with `+` or `Message.join`, which keep every part deferred. Call `resolve`
    only where a string is required now.

    For example, `tr(t"Saved.") + " " + tr(t"Undo is available.")` is one Message whose two
    sentences are translated separately when it renders. Bare strings are trusted Markdown.
    """

    template: str
    params: Mapping[str, object] = field(default_factory=dict)
    markup: Markup = Markup.DISCORD_MARKDOWN
    plural: str | None = None

    @classmethod
    def from_template(cls, template: Template) -> Message:
        """Create a deferred message with simple identifier placeholders.

        Preserve conversions and format specifications for render time. Expressions
        such as attribute access are rejected because catalogue keys need named
        parameters. Repeated names use the last interpolated value.
        """
        values: dict[str, object] = {}
        parts: list[str] = []
        for string, interpolation in zip(template.strings, template.interpolations, strict=False):
            name = interpolation.expression
            if not name.isidentifier():
                detail = f"template string interpolation {name!r} is not a placeholder name"
                raise ValueError(detail)
            conversion = "" if interpolation.conversion is None else f"!{interpolation.conversion}"
            format_spec = "" if not interpolation.format_spec else f":{interpolation.format_spec}"
            parts.append(string)
            parts.append(f"{{{name}{conversion}{format_spec}}}")
            values[name] = interpolation.value
        parts.append(template.strings[-1])
        return cls("".join(parts), values)

    @override
    def __str__(self) -> str:
        # Without this, an f-string or str() silently shows the dataclass repr to users.
        msg = f"Message {self.template!r} has no text until it is resolved in a localization"
        raise TypeError(msg)

    @override
    def __format__(self, format_spec: str) -> str:
        return str(self)

    def __add__(self, other: TextLike) -> Message:
        return Message.join("", (self, other))

    def __radd__(self, other: str | ResolvedText) -> Message:
        return Message.join("", (other, self))

    @staticmethod
    def join(separator: str, parts: Iterable[TextLike]) -> Message:
        """Join text with `separator`, keeping each part deferred until the render's locale.

        The result is not a catalogue entry; translators see each part on its own.
        """
        params = {f"part{index}": _as_resolved(part) for index, part in enumerate(parts)}
        literal = separator.replace("{", "{{").replace("}", "}}")
        return Message(literal.join(f"{{{key}}}" for key in params), params)

    def resolve(self) -> str:
        """Translate into the current localization now and return Discord Markdown.

        Use this only where a string is required: a discord.py argument that takes `str`,
        a JSON response, or text built inside a `localization_scope` chosen on purpose.
        """
        return discord_text(resolve_text(self, current_localization()))


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
        key: _nested(param, localization, value.markup) if isinstance(param, Message | ResolvedText) else param
        for key, param in value.params.items()
    }
    content = _resolve_named(template, params, value.markup) if params else template
    return ResolvedText(content, value.markup)


def _nested(value: Message | ResolvedText, localization: Localization, markup: Markup) -> RawMarkdown:
    """Resolve text interpolated into a Message, escaping plain text that lands in Markdown."""
    resolved = resolve_text(value, localization)
    return RawMarkdown(discord_text(resolved) if markup is Markup.DISCORD_MARKDOWN else resolved.content)


def _as_resolved[T: Message | ResolvedText](part: T | str) -> T | ResolvedText:
    """Treat a bare string as trusted Markdown, as everywhere a TextLike is accepted."""
    return ResolvedText(part) if isinstance(part, str) else part


def source_text(value: TextLike) -> str:
    """Return the untranslated source text, for logs, internal action names, and tests.

    Never show the result to a reader; renderers translate a Message into the reader's locale.
    """
    return resolve_text(value, NEUTRAL).content


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

    @override
    def __str__(self) -> str:
        return _safe_value(self.value, self.markup)

    @override
    def __repr__(self) -> str:
        return _safe_value(repr(self.value), self.markup)

    @override
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
    return _escape_markdown(str(value)).replace("@", "@\u200b")


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
    "source_text",
]
