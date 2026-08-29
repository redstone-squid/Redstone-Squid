"""Babel extraction extended for deferred Python template strings."""

import ast
import io
from collections.abc import Generator, Mapping, Sequence
from typing import Any

from babel.messages.extract import extract_python


def deferred_msgid(call: ast.Call) -> str | tuple[str, str] | None:
    """Derive the msgid carried by a deferred translation call, if statically known."""
    if not isinstance(call.func, ast.Name) or call.func.id not in {"L", "tr"} or not call.args:
        return None

    singular = _template_msgid(call.args[0])
    if singular is None:
        return None
    plural = next((keyword.value for keyword in call.keywords if keyword.arg == "plural"), None)
    if plural is None:
        return singular
    plural_msgid = _template_msgid(plural)
    return None if plural_msgid is None else (singular, plural_msgid)


def _template_msgid(message: ast.expr) -> str | None:
    if isinstance(message, ast.Constant) and isinstance(message.value, str):
        return message.value
    if not isinstance(message, ast.TemplateStr):
        return None
    parts: list[str] = []
    for value in message.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.Interpolation):
            conversion = "" if value.conversion == -1 else f"!{chr(value.conversion)}"
            if value.format_spec is None:
                format_spec = ""
            else:
                if not isinstance(value.format_spec, ast.JoinedStr):
                    return None
                format_parts: list[str] = []
                for part in value.format_spec.values:
                    if not isinstance(part, ast.Constant) or not isinstance(part.value, str):
                        return None
                    format_parts.append(part.value)
                format_spec = ":" + "".join(format_parts)
            parts.append("{" + value.str + conversion + format_spec + "}")
    return "".join(parts)


def extract_squid(
    fileobj: Any,
    keywords: Mapping[str, Any],
    comment_tags: Sequence[str],
    options: Any,
) -> Generator[Any]:
    """Extract ordinary Python messages plus msgids from deferred template calls."""
    data = fileobj.read()
    yield from extract_python(io.BytesIO(data), keywords, comment_tags, options)
    for node in ast.walk(ast.parse(data.decode("utf-8"))):
        if (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.TemplateStr)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"L", "tr"}
            and (msgid := deferred_msgid(node)) is not None
        ):
            yield node.lineno, node.func.id, msgid, []
