"""Cached source parsing for architecture tests."""

import ast
from functools import cache
from pathlib import Path


def source_tree(path: Path) -> ast.Module:
    """Cache one AST per resolved path for the life of this process.

    Later file edits are not detected. Callers share the returned tree and must not mutate it.
    """
    return _source_tree(path.resolve())


@cache
def _source_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
