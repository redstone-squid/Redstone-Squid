"""A CSS-inspired query syntax compiled to the typed selector operations."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import NoReturn, override

from squid_ui.authoring import semantic
from squid_ui.scene import model as scene_model
from squid_ui.scene import slack as scene_slack
from squid_ui.selectors.core import Selector, scope, select

_TOKEN = re.compile(r'\s+|"(?:[^"\\]|\\.)*"|[A-Za-z_][\w.-]*|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[\^$*]=|[*>+~\[\](),:=]')
_TYPES = {
    name: value
    for name, value in vars(semantic).items()
    if isinstance(value, type) and value.__module__ == semantic.__name__
}

_TYPES.update(
    {
        f"scene.{name}": value
        for module in (scene_model, scene_slack)
        for name, value in vars(module).items()
        if isinstance(value, type) and value.__module__ == module.__name__
    }
)


class SelectorSyntaxError(ValueError):
    """An unsupported or malformed selector, with a source offset and caret."""


@dataclass(frozen=True, slots=True)
class _Token:
    text: str
    start: int
    end: int


class _Parser:
    def __init__(self, source: str, types: Mapping[str, type]) -> None:
        self.source = source
        self.types = types
        self.tokens: list[_Token] = []
        self.index = 0
        self.depth = 0
        offset = 0
        while offset < len(source):
            match = _TOKEN.match(source, offset)
            if match is None:
                self.fail("unsupported character", offset)
            if not match[0].isspace():
                self.tokens.append(_Token(match[0], offset, match.end()))
            offset = match.end()
        self.tokens.append(_Token("", len(source), len(source)))

    def fail(self, message: str, offset: int | None = None) -> NoReturn:
        position = self.tokens[self.index].start if offset is None else offset
        detail = f"{message} at offset {position}\n{self.source}\n{' ' * position}^"
        raise SelectorSyntaxError(detail)

    def peek(self) -> str:
        return self.tokens[self.index].text

    def take(self) -> str:
        value = self.peek()
        if value:
            self.index += 1
        return value

    def expect(self, text: str) -> None:
        if self.peek() != text:
            self.fail(f"expected {text!r}")
        self.take()

    def selector_list(self, *, relative: bool = False) -> Selector[object]:
        result = self.complex(relative=relative)
        while self.peek() == ",":
            self.take()
            result = result.union(self.complex(relative=relative))
        return result

    def complex(self, *, relative: bool) -> Selector[object]:
        result = scope() if relative and self.peek() in (">", "+", "~") else self.compound()
        while self.peek() not in ("", ",", ")"):
            if self.peek() == ">":
                self.take()
                result = result.children(self.compound())
            elif self.peek() == "+":
                self.take()
                result = result.next_sibling(self.compound())
            elif self.peek() == "~":
                self.take()
                result = result.following_siblings(self.compound())
            elif self.tokens[self.index].start > self.tokens[self.index - 1].end:
                result = result.descendants(self.compound())
            else:
                self.fail("expected a child, descendant, or sibling combinator")
        return result

    def compound(self) -> Selector[object]:
        start = self.index
        token = self.peek()
        if token == "*":
            self.take()
            result = select(object)
        elif token and (token[0].isalpha() or token[0] == "_"):
            kind = self.types.get(token)
            if kind is None:
                self.fail(f"unknown type {token!r}; register it with types=")
            self.take()
            result = select(kind)
        elif token in ("[", ":"):
            result = select(object)
        else:
            self.fail("expected a type, *, attribute, or pseudo-class")
        while self.peek() in ("[", ":"):
            # Whitespace starts a descendant selector, not another predicate on this node.
            if self.index > start and self.tokens[self.index].start > self.tokens[self.index - 1].end:
                break
            if self.take() == "[":
                result = self.attribute(result)
            else:
                name = self.take()
                if name == "scope":
                    result = result.intersect(scope())
                    continue
                if name not in ("is", "not", "has"):
                    self.fail(f"unsupported pseudo-class {name!r}")
                self.expect("(")
                self.depth += 1
                if self.depth > 64:
                    self.fail("selector nesting exceeds 64 levels")
                inner = self.has_list() if name == "has" else self.selector_list()
                self.depth -= 1
                self.expect(")")
                if name == "not":
                    result = result.excluding(inner)
                else:
                    result = result.intersect(inner)
        return result

    def has_list(self) -> Selector[object]:
        following = self.peek() in ("+", "~")
        result = select(object)._has(self.complex(relative=True), direct=False, following=following)
        while self.peek() == ",":
            self.take()
            following = self.peek() in ("+", "~")
            result = result.union(select(object)._has(self.complex(relative=True), direct=False, following=following))
        return result

    def attribute(self, result: Selector[object]) -> Selector[object]:
        name = self.take()
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", name):
            self.fail("expected an attribute name")
        if self.peek() == "]":
            self.take()
            if name in ("key", "qualified_key"):
                return result.intersect(
                    Selector(
                        lambda tree, index, _scope, _locale: getattr(tree.matches[index], name) is not None,
                        f"[{name}]",
                    )
                )
            return result.has_attr("content" if name == "text" else name)
        operation = self.take()
        if operation not in ("=", "*=", "^=", "$="):
            self.fail("expected =, *=, ^=, or $=")
        token = self.take()
        try:
            value = json.loads(token)
        except ValueError, TypeError:
            self.fail("expected a double-quoted string, number, true, false, or null")
        self.expect("]")
        if name in ("text", "label"):
            if not isinstance(value, str):
                self.fail("text and label predicates require strings")
            method = result.text if name == "text" else result.label
            if operation == "^=":
                return method(re.compile(r"\A" + re.escape(value)))
            if operation == "$=":
                return method(re.compile(re.escape(value) + r"\Z"))
            return method(value, contains=operation == "*=")
        if operation != "=":
            if not isinstance(value, str):
                self.fail("string attribute operators require strings")
            match operation:
                case "^=":
                    return result.attr_starts_with(name, value)
                case "$=":
                    return result.attr_ends_with(name, value)
                case _:
                    return result.attr_contains(name, value)
        if name in ("key", "qualified_key"):
            if not isinstance(value, str):
                self.fail("key predicates require strings")
            return result.key(value) if name == "key" else result.qualified_key(value)
        return result.attr(name, value)


@dataclass(frozen=True, slots=True, eq=False)
class _RegisteredType:
    name: str
    kind: type

    @override
    def __hash__(self) -> int:
        return hash((self.name, id(self.kind)))

    @override
    def __eq__(self, other: object) -> bool:
        return isinstance(other, _RegisteredType) and self.name == other.name and self.kind is other.kind


@lru_cache(maxsize=256)
def _parse_cached(source: str, registered: tuple[_RegisteredType, ...]) -> Selector[object]:
    parser = _Parser(source, _TYPES | {entry.name: entry.kind for entry in registered})
    result = parser.selector_list()
    if parser.peek():
        parser.fail("unexpected token")
    return result


def parse(source: str, *, types: Mapping[str, type] | None = None) -> Selector[object]:
    """Compile type, attribute, relationship, :scope, :is, :not, and :has selectors.

    Semantic types and qualified scene.Type names are registered by default. Supply additional names for components
    and primitives; names never trigger imports. Attribute values use JSON literals.
    This is a CSS-inspired subset, without CSS escaping or specificity.

    Up to 256 compiled expressions are retained, keyed by source and registered type
    identities. Changing a registry mapping uses a separate entry. Matching results
    and syntax failures are not cached.
    """
    # Type identity matters even when a metaclass overloads equality or disables hashing.
    registered = tuple(_RegisteredType(name, kind) for name, kind in sorted(dict(types or {}).items()))
    return _parse_cached(source, registered)
