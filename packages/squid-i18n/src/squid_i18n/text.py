"""Prepare authored text for localization and target rendering.

Bare strings are trusted Discord Markdown. Use plain for literal text, or md to keep
template formatting while escaping interpolated values. Message defers translation
until the render's Localization is known; resolution produces ResolvedText carrying
both content and its markup type for the planner and renderer.
"""

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from string.templatelib import Interpolation, Template
from types import MappingProxyType
from typing import Any, LiteralString, Protocol, overload, override, runtime_checkable

from squid_i18n import runtime
from squid_i18n.compiler import Declaration, compile_template
from squid_i18n.errors import DeclarationError
from squid_i18n.expressions import DEVELOPER_OPTIONS, DateTimeExpression, NumberExpression


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
        return ResolvedText(discord_text(self) + discord_text(ResolvedText(other) if isinstance(other, str) else other))

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


@dataclass(frozen=True, slots=True)
class Localization:
    """An immutable catalog snapshot for a reader and its English fallback."""

    locale: str | None = None
    resources: tuple[str, ...] = ()
    source_resources: tuple[str, ...] = ()


NEUTRAL = Localization()
_CURRENT_LOCALIZATION: ContextVar[Localization] = ContextVar("squid_i18n_localization", default=NEUTRAL)


def current_localization() -> Localization:
    return _CURRENT_LOCALIZATION.get()


@contextmanager
def localization_scope(localization: Localization) -> Iterator[None]:
    token = _CURRENT_LOCALIZATION.set(localization)
    try:
        yield
    finally:
        _CURRENT_LOCALIZATION.reset(token)


@runtime_checkable
class TextValue(Protocol):
    def as_text(self, markup: Markup) -> ResolvedText: ...


@dataclass(frozen=True, slots=True)
class Message:
    """A named declaration with captured bindings, or a composition of deferred text."""

    declaration: Declaration | None
    bindings: Mapping[str, object] = field(default_factory=dict)
    markup: Markup = Markup.DISCORD_MARKDOWN
    selected_attribute: str | None = None
    parts: tuple[TextLike, ...] = ()
    separator: str = ""

    def attribute(self, name: LiteralString) -> Message:
        if self.declaration is None or name not in self.declaration.attributes:
            msg = f"Unknown attribute: {name}"
            raise DeclarationError(msg)
        return replace(self, selected_attribute=name)

    @override
    def __str__(self) -> str:
        msg = "A Message has no text until resolved in a localization"
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
        return Message(None, parts=tuple(parts), separator=separator)

    def resolve(self) -> str:
        return discord_text(resolve_text(self, current_localization()))


type TextLike = str | ResolvedText | Message


def tr(template: Template, /) -> Message:
    """Capture one named Fluent declaration; Python values never enter its parser."""
    if any(slot.conversion is not None or slot.format_spec for slot in template.interpolations):
        msg = "Use NUMBER or DATETIME instead of Python conversions and format specs"
        raise DeclarationError(msg)
    declaration = compile_template(template.strings, tuple(slot.expression for slot in template.interpolations))
    bindings: dict[str, object] = {}
    for slot, captured in zip(declaration.slots, template.interpolations, strict=True):
        value = captured.value
        if slot.function == "NUMBER" and not isinstance(value, NumberExpression):
            msg = "NUMBER must be the squid_i18n constructor"
            raise DeclarationError(msg)
        if slot.function == "DATETIME" and not isinstance(value, DateTimeExpression):
            msg = "DATETIME must be the squid_i18n constructor"
            raise DeclarationError(msg)
        if isinstance(value, NumberExpression | DateTimeExpression):
            developer = tuple(
                (key, option)
                for key, option in value.options
                if key in DEVELOPER_OPTIONS and (key, option) != ("style", "decimal")
            )
            value = replace(value, options=developer) if developer else value.value
        if slot.name in bindings and bindings[slot.name] is not value and bindings[slot.name] != value:
            msg = f"Conflicting captured values for {slot.name}"
            raise DeclarationError(msg)
        bindings[slot.name] = value
    return Message(declaration, MappingProxyType(bindings))


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
    if isinstance(value, ResolvedText):
        return value
    if isinstance(value, str):
        return ResolvedText(value)
    if value.declaration is None:
        parts = [resolve_text(part, localization) for part in value.parts]
        return ResolvedText(
            value.separator.join(
                discord_text(part) if value.markup is Markup.DISCORD_MARKDOWN else part.content for part in parts
            ),
            value.markup,
        )
    return runtime.render_message(value, localization)


def source_text(value: TextLike, *, markup: Markup | None = None) -> str:
    """Render English source independently of the active reader's locale."""
    if markup is not None and isinstance(value, Message | ResolvedText):
        value = replace(value, markup=markup)
    localization = current_localization()
    return resolve_text(value, Localization("en", source_resources=localization.source_resources)).content


def discord_text(value: MarkupText) -> str:
    return value.content if value.markup is Markup.DISCORD_MARKDOWN else _escape_markdown(value.content)


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
    if markup is Markup.PLAIN:
        return str(value)
    return _escape_markdown(str(value)).replace("@", "@\u200b")


def _temporal_value(value: object, markup: Markup) -> str | None:
    if isinstance(value, TextValue):
        return value.as_text(markup).content
    if isinstance(value, datetime):
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
