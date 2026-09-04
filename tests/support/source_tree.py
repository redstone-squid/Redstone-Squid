"""Cached source parsing for architecture tests, and the suite they walk."""

import ast
from functools import cache
from pathlib import Path

PACKAGE_SOURCE_ROOTS = (
    Path("packages/squid-ui/src"),
    Path("packages/squid-reactivity/src"),
    Path("packages/squid-replication/src"),
    Path("packages/squid-storage/src"),
    Path("packages/squid-ui-discord/src"),
    Path("packages/squid-ui-slack/src"),
    Path("packages/squid-ui-widgets/src"),
)

TERMINATING_VERBS = frozenset({"close", "detach", "finish", "cancel", "discard", "run"})
"""What ends something, and nothing else. See `docs/squid-ui-architecture.md`."""


def source_tree(path: Path) -> ast.Module:
    """Parse a source file once for the lifetime of the architecture test session."""
    return _source_tree(path.resolve())


@cache
def _source_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def classes_in_source() -> list[tuple[Path, ast.ClassDef]]:
    """Every class definition in the suite packages, nested ones included."""
    return [
        (path, node)
        for root in PACKAGE_SOURCE_ROOTS
        for path in sorted(root.rglob("*.py"))
        for node in ast.walk(source_tree(path))
        if isinstance(node, ast.ClassDef)
    ]
