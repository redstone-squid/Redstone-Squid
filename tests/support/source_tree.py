"""Cached source parsing for architecture tests."""

import ast
from functools import cache
from pathlib import Path


def source_tree(path: Path) -> ast.Module:
    """Parse a source file once for the lifetime of the architecture test session."""
    return _source_tree(path.resolve())


@cache
def _source_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
