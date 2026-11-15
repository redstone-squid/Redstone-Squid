"""Immutable typed selector expressions and strict query results."""

from collections.abc import Callable
from dataclasses import dataclass
from re import Pattern
from typing import Any, cast

from squid_ui.selectors.tree import Match, Snapshot
from squid_ui.text import NEUTRAL, Localization, TextLike, resolve_text

type Predicate = Callable[[Snapshot, int, int | None, Localization], bool]
_MISSING = object()


@dataclass(frozen=True, slots=True)
class Selector[T]:
    """A reusable read-only query expression. Type selection includes subclasses."""

    _predicate: Predicate
    description: str

    def attr(self, name: str, value: object) -> Selector[T]:
        """Match an exact attribute value; missing is distinct from None and False."""
        return self._filter(
            lambda node: (actual := getattr(node, name, _MISSING)) is not _MISSING and actual == value,
            f"[{name}={value!r}]",
        )

    def has_attr(self, name: str) -> Selector[T]:
        return self._filter(lambda node: hasattr(node, name), f"[{name}]")

    def where(self, predicate: Callable[[T], bool]) -> Selector[T]:
        """Add a Python predicate. Callers must keep it free of side effects."""
        return self._filter(predicate, ":where(predicate)")

    def _filter(self, predicate: Callable[[T], bool], description: str) -> Selector[T]:
        return Selector(
            lambda tree, index, scope, locale: (
                self._predicate(tree, index, scope, locale) and predicate(cast(T, tree.matches[index].node))
            ),
            self.description + description,
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
        )

    def text(self, value: str | Pattern[str], *, contains: bool = False) -> Selector[T]:
        """Match own content, resolving text with the query's localization; no whitespace folding."""
        return self._text("content", value, contains)

    def label(self, value: str | Pattern[str], *, contains: bool = False) -> Selector[T]:
        return self._text("label", value, contains)

    def _text(self, field: str, value: str | Pattern[str], contains: bool) -> Selector[T]:
        def matches(tree: Snapshot, index: int, scope: int | None, locale: Localization) -> bool:
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

        return Selector(matches, f"{self.description}[{field}{'*=' if contains else '='}{value!r}]")

    def intersect(self, other: Selector[Any]) -> Selector[T]:
        return Selector(
            lambda tree, index, scope, locale: (
                self._predicate(tree, index, scope, locale) and other._predicate(tree, index, scope, locale)
            ),
            f"({self.description} and {other.description})",
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
        )

    def descendants[U](self, other: Selector[U]) -> Selector[U]:
        return self._related(other, direct=False)

    def children[U](self, other: Selector[U]) -> Selector[U]:
        return self._related(other, direct=True)

    def _related[U](self, other: Selector[U], *, direct: bool) -> Selector[U]:
        def matches(tree: Snapshot, index: int, scope: int | None, locale: Localization) -> bool:
            if not other._predicate(tree, index, scope, locale) or index == scope:
                return False
            parent = tree.parents[index]
            while parent is not None:
                if self._predicate(tree, parent, scope, locale):
                    return True
                if direct or parent == scope:
                    break
                parent = tree.parents[parent]
            return False

        return Selector(matches, f"{self.description}{' > ' if direct else ' '}{other.description}")

    def has(self, other: Selector[Any], *, direct: bool = False) -> Selector[T]:
        """Keep nodes with a matching descendant, evaluating the inner query in that node's scope."""
        return Selector(
            lambda tree, index, scope, locale: (
                self._predicate(tree, index, scope, locale)
                and any(
                    (not direct or tree.parents[child] == index) and other._predicate(tree, child, index, locale)
                    for child in range(index + 1, tree.ends[index])
                )
            ),
            f"{self.description}:has({' > ' if direct else ''}{other.description})",
        )


def select[T](kind: type[T]) -> Selector[T]:
    return Selector(lambda tree, index, _scope, _locale: isinstance(tree.matches[index].node, kind), kind.__name__)


def scope() -> Selector[object]:
    """Match the explicit query scope; use include_scope=True to return it."""
    return Selector(lambda _tree, index, root, _locale: index == root, ":scope")


class QueryError(LookupError):
    """A query requiring zero or one, or exactly one occurrence could not be satisfied."""


@dataclass(frozen=True, slots=True)
class Query[T]:
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


def query[T](
    tree: Snapshot,
    selector: Selector[T],
    *,
    within: Match[Any] | None = None,
    include_scope: bool = False,
    localization: Localization = NEUTRAL,
) -> Query[T]:
    """Evaluate once in depth-first order, deduplicating by occurrence, not object identity.

    An unscoped query includes every root. A scoped query includes strict descendants
    unless include_scope is set. Ancestor and has predicates cannot escape the scope.
    """
    root = tree.index(within) if within is not None else None
    start = 0 if root is None else root + (not include_scope)
    end = len(tree.matches) if root is None else tree.ends[root]
    available = tree.matches[start:end]
    found = tuple(
        cast(Match[T], tree.matches[index])
        for index in range(start, end)
        if selector._predicate(tree, index, root, localization)
    )
    return Query(found, selector.description, available)
