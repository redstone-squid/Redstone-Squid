"""Localized text with explicit formatting.

Strings and translations are literal by default. md opts into shared Markdown;
discord_md explicitly accepts Discord syntax. Interpolations preserve typed text
and escape ordinary values. Discord serialization belongs at the target boundary.
"""

import re
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
    MARKDOWN = "markdown"
    DISCORD_MARKDOWN = "discord-markdown"


@dataclass(frozen=True, slots=True)
class ResolvedText:
    """Resolved source content and its explicit markup dialect."""

    content: str
    markup: Markup = Markup.PLAIN

    def __add__(self, other: TextLike) -> Message:
        return Message.join("", (self, other))

    def __radd__(self, other: str) -> Message:
        return Message.join("", (other, self))

    def resolve(self) -> str:
        """Return resolved source content without choosing an output platform."""
        return self.content


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
    markup: Markup = Markup.PLAIN
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
        return resolve_text(self, current_localization()).content


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
            if slot.function is None:
                msg = "Call NUMBER or DATETIME directly in the declaration so extraction sees its options"
                raise DeclarationError(msg)
            developer = tuple(
                (key, option)
                for key, option in value.options
                if key in DEVELOPER_OPTIONS and (key, option) != ("style", "decimal")
            )
            value = (
                replace(value, options=developer) if developer or isinstance(value, DateTimeExpression) else value.value
            )
        if slot.name in bindings and bindings[slot.name] is not value and bindings[slot.name] != value:
            msg = f"Conflicting captured values for {slot.name}"
            raise DeclarationError(msg)
        bindings[slot.name] = value
    return Message(declaration, MappingProxyType(bindings))


def md_tr(template: Template, /) -> Message:
    """Declare localized Markdown with literal-by-default interpolation values."""
    return replace(tr(template), markup=Markup.MARKDOWN)


def discord_md(value: str | Template) -> ResolvedText:
    """Accept explicitly authored Discord syntax, including mentions and custom emoji."""
    return ResolvedText(
        _resolve_template(value, Markup.DISCORD_MARKDOWN) if isinstance(value, Template) else value,
        Markup.DISCORD_MARKDOWN,
    )


def plain(value: object) -> ResolvedText:
    """Create literal text which renderers must not interpret as Markdown."""
    if isinstance(value, Template):
        return ResolvedText(_resolve_template(value, Markup.PLAIN), Markup.PLAIN)
    temporal = _temporal_value(value, Markup.PLAIN)
    return ResolvedText(str(value) if temporal is None else temporal, Markup.PLAIN)


@overload
def md(value: Message, /) -> Message: ...
@overload
def md(value: str | Template, /, **values: object) -> ResolvedText: ...
def md(value: str | Template | Message, /, **values: object) -> ResolvedText | Message:
    """Author Markdown, escaping ordinary bindings and preserving typed text.

    Wrap tr(...) to declare a localized Markdown message without changing extraction.
    """
    if isinstance(value, Message):
        if values:
            message = "localized messages already contain their bindings"
            raise TypeError(message)
        return replace(value, markup=Markup.MARKDOWN)
    if isinstance(value, Template):
        if values:
            message = "template strings already contain their interpolation values"
            raise TypeError(message)
        return ResolvedText(_resolve_template(value, Markup.MARKDOWN), Markup.MARKDOWN)
    return ResolvedText(_resolve_named(value, values, Markup.MARKDOWN) if values else value, Markup.MARKDOWN)


def resolve_text(value: TextLike, localization: Localization) -> ResolvedText:
    if isinstance(value, ResolvedText):
        return value
    if isinstance(value, str):
        return ResolvedText(value)
    if value.declaration is None:
        parts = [resolve_text(part, localization) for part in value.parts]
        markup = (
            Markup.DISCORD_MARKDOWN
            if any(part.markup is Markup.DISCORD_MARKDOWN for part in parts)
            else Markup.MARKDOWN
            if value.markup is Markup.MARKDOWN or any(part.markup is Markup.MARKDOWN for part in parts)
            else Markup.PLAIN
        )
        separator = _escape_markdown(value.separator) if markup is not Markup.PLAIN else value.separator
        return ResolvedText(separator.join(_content_for(part, markup) for part in parts), markup)
    return runtime.render_message(value, localization)


def source_text(value: TextLike, *, markup: Markup | None = None) -> str:
    """Render English source independently of the active reader's locale."""
    if markup is not None and isinstance(value, Message | ResolvedText):
        value = replace(value, markup=markup)
    localization = current_localization()
    return resolve_text(value, Localization("en", source_resources=localization.source_resources)).content


def discord_text(value: MarkupText) -> str:
    if value.markup is not Markup.PLAIN:
        return value.content
    escaped = re.sub(r"(?m)([\\`*_~|<\[])|^([>#-])", r"\\\g<0>", value.content)
    return escaped.replace("@", "@\u200b")


def _content_for(value: MarkupText, markup: Markup) -> str:
    if markup is Markup.PLAIN or value.markup is not Markup.PLAIN:
        return value.content
    return _escape_markdown(value.content).replace("@", "@\u200b")


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
        if isinstance(self.value, Message | ResolvedText):
            resolved = resolve_text(self.value, current_localization())
            return _content_for(replace(resolved, content=format(resolved.content, format_spec)), self.markup)
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
        if isinstance(value, Message | ResolvedText):
            resolved = resolve_text(value, current_localization())
            value = replace(resolved, content=format(resolved.content, interpolation.format_spec))
        else:
            value = format(value, interpolation.format_spec)
    return _safe_value(value, markup)


def _safe_value(value: object, markup: Markup) -> str:
    if isinstance(value, Message | ResolvedText):
        return _content_for(resolve_text(value, current_localization()), markup)
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
    if markup is not Markup.DISCORD_MARKDOWN:
        return instant.isoformat()
    return f"<t:{int(instant.timestamp())}:{style}>"


def _escape_markdown(value: str) -> str:
    escaped: list[str] = []
    for character in value:
        if character in "\\`*_{}[]()<>#+-.!|~":
            escaped.append("\\")
        escaped.append(character)
    return "".join(escaped)
