"""Deferred text with literal bindings and one shared Markdown source syntax.

Text is independent of its eventual destination. Translations resolve for the reader
before their authored formatting is parsed; captured values remain separate nodes.
"""

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from string import Formatter
from string.templatelib import Template
from types import MappingProxyType
from typing import Literal, LiteralString, Protocol, override, runtime_checkable

from squid_i18n import runtime, tree
from squid_i18n.compiler import Declaration, compile_template
from squid_i18n.errors import DeclarationError
from squid_i18n.expressions import DEVELOPER_OPTIONS, DateTimeExpression, NumberExpression

type SourceFormat = Literal["plain", "markdown"]


class Markup(StrEnum):
    """Encoding of serialized scene content, never an authored text dialect."""

    PLAIN = "plain"
    MARKDOWN = "markdown"
    DISCORD_MARKDOWN = "discord-markdown"


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


@dataclass(frozen=True, slots=True)
class _Translation:
    declaration: Declaration
    bindings: Mapping[str, object]
    markdown: bool
    selected_attribute: str | None = None


@dataclass(frozen=True, slots=True)
class _Markdown:
    parts: tuple[str | Text, ...]


@dataclass(frozen=True, slots=True)
class _Style:
    style: Literal["strong", "em"]
    child: TextLike


@dataclass(frozen=True, slots=True)
class _Link:
    label: TextLike
    destination: str


@dataclass(frozen=True, slots=True)
class _Code:
    child: TextLike


@dataclass(frozen=True, slots=True)
class _Mention:
    kind: Literal["user", "role", "channel"]
    identifier: int


@dataclass(frozen=True, slots=True)
class _Timestamp:
    instant: datetime
    style: Literal["t", "T", "d", "D", "f", "F", "R"]


type _Part = str | _Translation | _Markdown | _Style | _Link | _Code | _Mention | _Timestamp


@dataclass(frozen=True, slots=True)
class Text:
    """Composable text; use tr, md, plain, or a formatting constructor to create it.

    Ordinary strings are literal. Concatenation preserves each fragment's structure
    and defers translation until rendering for a reader.
    """

    _parts: tuple[_Part, ...]

    def __add__(self, other: TextLike) -> Text:
        return Text(self._parts + ((other,) if isinstance(other, str) else other._parts))

    def __radd__(self, other: str) -> Text:
        return Text((other, *self._parts))

    @staticmethod
    def join(separator: TextLike, parts: Iterable[TextLike]) -> Text:
        result: list[_Part] = []
        for index, part in enumerate(parts):
            if index:
                result.extend((separator,) if isinstance(separator, str) else separator._parts)
            result.extend((part,) if isinstance(part, str) else part._parts)
        return Text(tuple(result))

    @property
    def declaration(self) -> Declaration | None:
        """The declaration for a standalone translation, if this text is one."""
        part = self._parts[0] if len(self._parts) == 1 else None
        return part.declaration if isinstance(part, _Translation) else None

    @property
    def bindings(self) -> Mapping[str, object]:
        part = self._parts[0] if len(self._parts) == 1 else None
        return part.bindings if isinstance(part, _Translation) else MappingProxyType({})

    def attribute(self, name: LiteralString) -> Text:
        part = self._parts[0] if len(self._parts) == 1 else None
        if not isinstance(part, _Translation) or name not in part.declaration.attributes:
            msg = f"Unknown attribute: {name}"
            raise DeclarationError(msg)
        return Text((replace(part, selected_attribute=name),))

    def resolve(self) -> str:
        return resolve_text(self, current_localization()).content

    @override
    def __str__(self) -> str:
        msg = "Text has no text until resolved in a localization"
        raise TypeError(msg)

    @override
    def __format__(self, format_spec: str) -> str:
        return str(self)


type TextLike = str | Text


@runtime_checkable
class TextValue(Protocol):
    def as_text(self) -> Text: ...


@dataclass(frozen=True, slots=True)
class _Document:
    """Resolved structure. Serialized views are derived, never stored alongside it."""

    nodes: tuple[tree.Node, ...]

    @property
    def markup(self) -> Markup:
        return Markup.MARKDOWN if any(tree.formatted(node) for node in self.nodes) else Markup.PLAIN

    @property
    def content(self) -> str:
        return self.plain() if self.markup is Markup.PLAIN else tree.markdown(self.nodes)

    def plain(self) -> str:
        return "".join(tree.plain(node) for node in self.nodes)


def tr(template: Template | LiteralString, /, *, key: LiteralString | None = None, markdown: bool = False) -> Text:
    """Capture a Fluent declaration with literal bindings and explicit source syntax.

    Supply its identity with key= or an embedded Fluent identifier. Markdown is
    parsed after translation, independently of the eventual rendering target.
    Use !s for literal identifiers and !r for diagnostics; unconverted numbers retain locale formatting.
    """
    if not isinstance(markdown, bool):
        msg = "markdown must be a bool"
        raise DeclarationError(msg)
    if isinstance(template, str):
        template = Template(template)
    if any(slot.format_spec for slot in template.interpolations):
        msg = "Use NUMBER or DATETIME instead of Python format specs"
        raise DeclarationError(msg)
    declaration = compile_template(
        template.strings,
        tuple(slot.expression for slot in template.interpolations),
        key,
        conversions=tuple(slot.conversion for slot in template.interpolations),
    )
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
        if captured.conversion:
            value = Formatter().convert_field(value, captured.conversion)
        if slot.name in bindings and bindings[slot.name] is not value and bindings[slot.name] != value:
            msg = f"Conflicting captured values for {slot.name}"
            raise DeclarationError(msg)
        bindings[slot.name] = value
    return Text((_Translation(declaration, MappingProxyType(bindings), markdown),))


def md_tr(template: Template | LiteralString, /, *, key: LiteralString | None = None) -> Text:
    """Declare translated Markdown using the same deferred Text representation."""
    return tr(template, key=key, markdown=True)


def plain(value: object) -> Text:
    """Capture literal text, or a template whose bindings remain deferred."""
    if isinstance(value, Template):
        parts: list[TextLike] = []
        for string, slot in zip(value.strings, value.interpolations, strict=False):
            parts.extend((string, _capture(slot.value, slot.conversion, slot.format_spec)))
        parts.append(value.strings[-1])
        return Text.join("", parts)
    return Text((value.isoformat() if isinstance(value, datetime) else str(value),))


def _capture(value: object, conversion: str | None = None, spec: str = "") -> Text:
    if isinstance(value, TextValue):
        value = value.as_text()
    if isinstance(value, datetime):
        value = timestamp_text(value)
    if isinstance(value, Text):
        if conversion or spec:
            msg = "Format a Python value before constructing Text; Text stays deferred"
            raise TypeError(msg)
        return value
    if conversion:
        value = Formatter().convert_field(value, conversion)
    return plain(format(value, spec) if spec else value)


def md(value: str | Template, /, **values: object) -> Text:
    """Capture a complete Markdown fragment; interpolated strings remain literal."""
    parts: list[str | Text] = []
    if isinstance(value, Template):
        if values:
            msg = "template strings already contain their bindings"
            raise TypeError(msg)
        for string, slot in zip(value.strings, value.interpolations, strict=False):
            parts.extend((string, _capture(slot.value, slot.conversion, slot.format_spec)))
        parts.append(value.strings[-1])
    elif values:
        for literal, name, spec, conversion in Formatter().parse(value):
            parts.append(literal)
            if name is not None:
                parts.append(_capture(values[name], conversion, spec or ""))
    else:
        parts.append(value)
    return Text((_Markdown(tuple(parts)),))


def resolve_text(value: TextLike, localization: Localization) -> _Document:
    if isinstance(value, str):
        return _Document((tree.literal(value),))
    nodes: list[tree.Node] = []
    for part in value._parts:
        match part:
            case str():
                nodes.append(tree.literal(part))
            case _Translation():
                nodes.extend(runtime.render_message(part, localization).nodes)
            case _Markdown():
                nodes.extend(
                    tree.parse(
                        [
                            item
                            if isinstance(item, str)
                            else tree.Node("sequence", children=resolve_text(item, localization).nodes)
                            for item in part.parts
                        ]
                    )
                )
            case _Style():
                nodes.append(
                    tree.Node(
                        part.style,
                        children=resolve_text(part.child, localization).nodes,
                        tag=part.style,
                        markup="**" if part.style == "strong" else "*",
                    )
                )
            case _Link():
                nodes.append(
                    tree.Node(
                        "link",
                        children=resolve_text(part.label, localization).nodes,
                        tag="a",
                        attrs=(("href", part.destination),),
                    )
                )
            case _Code():
                nodes.append(tree.Node("code_inline", resolve_text(part.child, localization).plain(), tag="code"))
            case _Mention():
                nodes.append(tree.Node("mention", str(part.identifier), attrs=(("kind", part.kind),)))
            case _Timestamp():
                nodes.append(
                    tree.Node(
                        "timestamp",
                        part.instant.isoformat(),
                        attrs=(("unix", str(int(part.instant.timestamp()))), ("style", part.style)),
                    )
                )
    return _Document(tuple(nodes))


def source_text(value: TextLike, *, format: SourceFormat | None = None) -> str:
    """Render English source independently of the reader; format='plain' removes styling."""
    localization = current_localization()
    resolved = resolve_text(value, Localization("en", source_resources=localization.source_resources))
    if format == "plain":
        return resolved.plain()
    return tree.markdown(resolved.nodes) if format == "markdown" else resolved.content


class _SceneText(Protocol):
    @property
    def content(self) -> str: ...

    @property
    def markup(self) -> Markup: ...


def discord_text(value: _Document | _SceneText, *, escape: Callable[[str], str] | None = None) -> str:
    """Serialize resolved structure, or an already encoded scene, for Discord."""
    if isinstance(value, _Document):
        return tree.markdown(value.nodes, discord=True, escape=escape)
    if value.markup is Markup.DISCORD_MARKDOWN:
        return value.content
    nodes = (tree.literal(value.content),) if value.markup is Markup.PLAIN else tree.parse([value.content])
    return tree.markdown(nodes, discord=True, escape=escape)


def strong(value: TextLike) -> Text:
    """Apply strong emphasis without interpreting the child as Markdown."""
    return Text((_Style("strong", value),))


def emphasis(value: TextLike) -> Text:
    """Apply emphasis without interpreting the child as Markdown."""
    return Text((_Style("em", value),))


def inline_code(value: TextLike) -> Text:
    """Display the child as literal code, with delimiters chosen during rendering."""
    return Text((_Code(value),))


def text_link(label: TextLike, destination: str) -> Text:
    """Attach a safe link destination to a text label."""
    if not tree.valid_link(destination):
        msg = "Text links require a safe destination"
        raise ValueError(msg)
    return Text((_Link(label, destination),))


def mention(identifier: int, *, kind: Literal["user", "role", "channel"] = "user") -> Text:
    """Refer to a Discord entity; other destinations display its identifier."""
    if identifier <= 0 or kind not in ("user", "role", "channel"):
        msg = "A mention requires a positive identifier and a user, role, or channel kind"
        raise ValueError(msg)
    return Text((_Mention(kind, identifier),))


def timestamp_text(instant: datetime, *, style: Literal["t", "T", "d", "D", "f", "F", "R"] = "f") -> Text:
    if instant.tzinfo is None or instant.utcoffset() is None:
        msg = "timestamp interpolation requires an aware datetime"
        raise ValueError(msg)
    if style not in ("t", "T", "d", "D", "f", "F", "R"):
        msg = "Unknown timestamp style"
        raise ValueError(msg)
    return Text((_Timestamp(instant, style),))
