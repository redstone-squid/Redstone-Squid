"""Extract attribute docstrings for SQLAlchemy column comments.

Python does not attach bare string literals following an attribute assignment to
anything at runtime (unlike class/function docstrings), so we recover them by
parsing the class's own source with `ast`.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any, override


class _AttributeDocstringVisitor(ast.NodeVisitor):
    """Collect `attr: Type = ...` followed by a bare string literal."""

    def __init__(self) -> None:
        self.docstrings: dict[str, str] = {}
        self._pending_target: str | None = None

    @override
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._pending_target = node.target.id if isinstance(node.target, ast.Name) else None

    @override
    def visit_Expr(self, node: ast.Expr) -> None:
        if (
            self._pending_target is not None
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            self.docstrings[self._pending_target] = inspect.cleandoc(node.value.value)
        self._pending_target = None

    @override
    def generic_visit(self, node: ast.AST) -> None:
        # Any other statement breaks the "docstring immediately follows the attribute" rule.
        # Deliberately does not recurse into `node`'s children: we only care about
        # attributes declared directly in the class body, not nested statements.
        self._pending_target = None


def extract_attribute_docstrings(cls: type[Any]) -> dict[str, str]:
    """Return a mapping of attribute name to its trailing docstring, if any.

    Only docstrings declared directly in *cls* (not inherited ones) are returned.
    """
    try:
        source = inspect.getsource(cls)
    except (OSError, TypeError):
        return {}

    tree = ast.parse(textwrap.dedent(source))
    class_def = tree.body[0]
    if not isinstance(class_def, ast.ClassDef):
        return {}

    visitor = _AttributeDocstringVisitor()
    for statement in class_def.body:
        visitor.visit(statement)
    return visitor.docstrings
