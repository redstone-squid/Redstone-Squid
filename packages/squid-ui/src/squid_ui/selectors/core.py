"""Immutable typed selector expressions and strict query results."""

from collections.abc import Callable
from dataclasses import dataclass, field
from re import Pattern
from typing import Any, cast, overload

from squid_ui.content.text import Localization, TextLike, resolve_text
from squid_ui.errors import SquidUiError
from squid_ui.selectors.tree import Match, TreeIndex

type Predicate = Callable[[TreeIndex, int, int | None, Localization], bool]
_MISSING = object()


def _attribute(match: Match[object], name: str) -> object:
    if name in ("key", "qualified_key"):
        value = getattr(match, name)
        return _MISSING if value is None else value
    value = match.node
    for part in name.split("."):
        value = getattr(value, part, _MISSING)
        if value is _MISSING:
            break
    return value


@dataclass(frozen=True, slots=True)
class Selector[T]:
    """A reusable read-only query expression. Type selection includes subclasses."""

    _predicate: Predicate
    description: str
    _kind: type | None = field(default=None, repr=False, compare=False)
    _key: tuple[str, str] | None = field(default=None, repr=False, compare=False)

    def attr(self, name: str, value: object) -> Selector[T]:
        """Match an attribute path; missing is distinct from None and False.

        Dots follow attributes, such as overflow.key. key and qualified_key refer to
        occurrence metadata, including component placement keys.
        """
        if name in ("key", "qualified_key") and isinstance(value, str):
            return self._metadata(name, value)
        return self._attribute_filter(name, lambda actual: actual is not _MISSING and actual == value, f"={value!r}")

    def has_attr(self, name: str) -> Selector[T]:
        return self._attribute_filter(name, lambda actual: actual is not _MISSING, "")

    def attr_starts_with(self, name: str, value: str) -> Selector[T]:
        return self._attribute_filter(
            name, lambda actual: isinstance(actual, str) and actual.startswith(value), f"^={value!r}"
        )

    def attr_ends_with(self, name: str, value: str) -> Selector[T]:
        return self._attribute_filter(
            name, lambda actual: isinstance(actual, str) and actual.endswith(value), f"$={value!r}"
        )

    def attr_contains(self, name: str, value: str) -> Selector[T]:
        return self._attribute_filter(name, lambda actual: isinstance(actual, str) and value in actual, f"*={value!r}")

    def _attribute_filter(self, name: str, predicate: Callable[[object], bool], suffix: str) -> Selector[T]:
        return Selector(
            lambda tree, index, scope, locale: (
                self._predicate(tree, index, scope, locale) and predicate(_attribute(tree.matches[index], name))
            ),
            f"{self.description}[{name}{suffix}]",
            self._kind,
            self._key,
        )

    def where(self, predicate: Callable[[T], bool]) -> Selector[T]:
        """Add a Python predicate. Callers must keep it free of side effects."""
        return self._filter(predicate, ":where(predicate)")

    def _filter(self, predicate: Callable[[T], bool], description: str) -> Selector[T]:
        return Selector(
            lambda tree, index, scope, locale: (
                self._predicate(tree, index, scope, locale) and predicate(cast(T, tree.matches[index].node))
            ),
            self.description + description,
            self._kind,
            self._key,
        )

    def key(self, value: str) -> Selector[T]:
        return self._metadata("key", value)

    def qualified_key(self, value: str) -> Selector[T]:
        return self._metadata("qualified_key", value)

    def _metadata(self, name: str, value: str) -> Selector[T]:
        return Selector(
            lambda tree, index, scope, locale: (
                self._predicate(tree, index, scope, locale) and getattr(tree.matches[index], name) == value
            ),
            f"{self.description}[{name}={value!r}]",
            self._kind,
            (name, value),
        )

    def text(self, value: str | Pattern[str], *, contains: bool = False) -> Selector[T]:
        """Match own content, resolving text with the query's localization; no whitespace folding."""
        return self._text("content", value, contains)

    def label(self, value: str | Pattern[str], *, contains: bool = False) -> Selector[T]:
        return self._text("label", value, contains)

    def _text(self, field: str, value: str | Pattern[str], contains: bool) -> Selector[T]:
        def matches(tree: TreeIndex, index: int, scope: int | None, locale: Localization) -> bool:
            if not self._predicate(tree, index, scope, locale):
                return False
            raw = getattr(tree.matches[index].node, field, None)
            if raw is None:
                return False
            # ItemLabel and similar UI wrappers designate their own content as the label.
            raw = getattr(raw, "content", raw)
            content = resolve_text(cast(TextLike, raw), locale).content
            if isinstance(value, Pattern):
                return value.search(content) is not None
            return value in content if contains else value == content

        return Selector(
            matches, f"{self.description}[{field}{'*=' if contains else '='}{value!r}]", self._kind, self._key
        )

    def intersect(self, other: Selector[Any]) -> Selector[T]:
        return Selector(
            lambda tree, index, scope, locale: (
                self._predicate(tree, index, scope, locale) and other._predicate(tree, index, scope, locale)
            ),
            f"({self.description} and {other.description})",
            self._kind or other._kind,
            self._key or other._key,
        )

    def union[U](self, other: Selector[U]) -> Selector[T | U]:
        return Selector(
            lambda tree, index, scope, locale: (
                self._predicate(tree, index, scope, locale) or other._predicate(tree, index, scope, locale)
            ),
            f"{self.description}, {other.description}",
        )

    def excluding(self, other: Selector[Any]) -> Selector[T]:
        return Selector(
            lambda tree, index, scope, locale: (
                self._predicate(tree, index, scope, locale) and not other._predicate(tree, index, scope, locale)
            ),
            f"{self.description}:not({other.description})",
            self._kind,
            self._key,
        )

    def descendants[U](self, other: Selector[U]) -> Selector[U]:
        return self._related(other, direct=False)

    def children[U](self, other: Selector[U]) -> Selector[U]:
        return self._related(other, direct=True)

    def _related[U](self, other: Selector[U], *, direct: bool) -> Selector[U]:
        def matches(tree: TreeIndex, index: int, scope: int | None, locale: Localization) -> bool:
            if not other._predicate(tree, index, scope, locale) or index == scope:
                return False
            parent = tree.parents[index]
            while parent is not None:
                if scope is not None and parent < scope:
                    break
                if self._predicate(tree, parent, scope, locale):
                    return True
                if direct or parent == scope:
                    break
                parent = tree.parents[parent]
            return False

        return Selector(
            matches, f"{self.description}{' > ' if direct else ' '}{other.description}", other._kind, other._key
        )

    def next_sibling[U](self, other: Selector[U]) -> Selector[U]:
        """Select an immediately following sibling; descendants do not count as siblings."""
        return self._siblings(other, adjacent=True)

    def following_siblings[U](self, other: Selector[U]) -> Selector[U]:
        """Select later siblings with the same parent, in tree order."""
        return self._siblings(other, adjacent=False)

    def _siblings[U](self, other: Selector[U], *, adjacent: bool) -> Selector[U]:
        def matches(tree: TreeIndex, index: int, scope: int | None, locale: Localization) -> bool:
            if index == scope or not other._predicate(tree, index, scope, locale):
                return False
            parent = tree.parents[index]
            start = 0 if parent is None else parent + 1
            if scope is not None:
                start = max(start, scope)
            # Walk previous occurrences backwards, skipping their descendants.
            for previous in range(index - 1, start - 1, -1):
                if tree.parents[previous] != parent:
                    continue
                if self._predicate(tree, previous, scope, locale):
                    return True
                if adjacent:
                    return False
            return False

        return Selector(
            matches, f"{self.description}{' + ' if adjacent else ' ~ '}{other.description}", other._kind, other._key
        )

    def has(self, other: Selector[Any], *, direct: bool = False) -> Selector[T]:
        """Keep nodes with a matching descendant, evaluating the inner query in that node's scope."""
        return self._has(other, direct=direct, following=False)

    def _has(self, other: Selector[Any], *, direct: bool, following: bool) -> Selector[T]:
        def matches(tree: TreeIndex, index: int, scope: int | None, locale: Localization) -> bool:
            if not self._predicate(tree, index, scope, locale):
                return False
            if following:
                # Relative +/~ can inspect following siblings, but never siblings of the query scope.
                if index == scope:
                    return False
                parent = tree.parents[index]
                start = tree.ends[index]
                end = len(tree.matches) if parent is None else tree.ends[parent]
            else:
                start, end = index + 1, tree.ends[index]
            return any(
                (not direct or tree.parents[child] == index) and other._predicate(tree, child, index, locale)
                for child in range(start, end)
            )

        return Selector(
            matches, f"{self.description}:has({' > ' if direct else ''}{other.description})", self._kind, self._key
        )


@overload
def select[T](target: type[T]) -> Selector[T]: ...


@overload
def select[T](target: T) -> Selector[T]: ...


def select[T](target: type[T] | T) -> Selector[T]:
    """Match a type (including subclasses) or an exact instance by identity.

    An instance matches every occurrence of that object. Narrow by qualified_key
    or query scope to identify one placement.
    """
    if isinstance(target, type):
        return Selector(
            lambda tree, index, _scope, _locale: isinstance(tree.matches[index].node, target),
            target.__name__,
            target if type(target) is type else None,
        )
    return Selector(
        lambda tree, index, _scope, _locale: tree.matches[index].node is target,
        f"{type(target).__name__}(instance@{id(target):x})",
    )


def scope() -> Selector[object]:
    """Match the explicit query scope; use include_scope=True to return it."""
    return Selector(lambda _tree, index, root, _locale: index == root, ":scope")


class QueryError(SquidUiError, LookupError):
    """A query requiring zero or one, or exactly one occurrence could not be satisfied."""


@dataclass(frozen=True, slots=True)
class QuerySnapshot[T]:
    _matches: tuple[Match[T], ...]
    _description: str
    _available: tuple[Match[object], ...]

    def all(self) -> tuple[Match[T], ...]:
        return self._matches

    def count(self) -> int:
        return len(self._matches)

    def exists(self) -> bool:
        return bool(self._matches)

    def one(self) -> Match[T]:
        if len(self._matches) != 1:
            msg = "exactly one"
            raise self._error(msg)
        return self._matches[0]

    def optional(self) -> Match[T] | None:
        if len(self._matches) > 1:
            msg = "zero or one"
            raise self._error(msg)
        return self._matches[0] if self._matches else None

    def _error(self, expected: str) -> QueryError:
        candidates = self._matches or self._available
        details = "\n".join(
            f"  {match.path}: {type(match.node).__name__} key={match.key!r} owner={match.owner!r}"
            for match in candidates[:20]
        )
        if len(candidates) > 20:
            details += f"\n  ... {len(candidates) - 20} more"
        return QueryError(f"Expected {expected} {self._description}; found {len(self._matches)}.\n{details}")


@dataclass(frozen=True, slots=True)
class Query[T]:
    """A reusable query. Capture it to pin several reads to one tree."""

    _resolve: Callable[[], QuerySnapshot[T]]

    def capture(self) -> QuerySnapshot[T]:
        return self._resolve()

    def all(self) -> tuple[Match[T], ...]:
        return self.capture().all()

    def count(self) -> int:
        return self.capture().count()

    def exists(self) -> bool:
        return self.capture().exists()

    def one(self) -> Match[T]:
        return self.capture().one()

    def optional(self) -> Match[T] | None:
        return self.capture().optional()
