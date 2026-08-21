"""Babel extraction extended for deferred Python template strings."""

import ast
import io
from collections.abc import Generator, Mapping, Sequence
from typing import Any

from babel.messages.extract import extract_python


def deferred_msgid(call: ast.Call) -> str | None:
    """Derive the msgid carried by an ``L(...)`` call, if it is statically known."""
    if not isinstance(call.func, ast.Name) or call.func.id != "L" or not call.args:
        return None
    message = call.args[0]
    if isinstance(message, ast.Constant) and isinstance(message.value, str):
        return message.value
    if not isinstance(message, ast.TemplateStr):
        return None
    parts: list[str] = []
    for value in message.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.Interpolation):
            parts.append("{" + value.str + "}")
    return "".join(parts)


def extract_squid(
    fileobj: Any,
    keywords: Mapping[str, Any],
    comment_tags: Sequence[str],
    options: Any,
) -> Generator[Any]:
    """Extract ordinary Python messages plus msgids from ``L(t\"...\")`` calls."""
    data = fileobj.read()
    yield from extract_python(io.BytesIO(data), keywords, comment_tags, options)
    for node in ast.walk(ast.parse(data.decode("utf-8"))):
        if (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.TemplateStr)
            and (msgid := deferred_msgid(node)) is not None
        ):
            yield node.lineno, "L", msgid, []
