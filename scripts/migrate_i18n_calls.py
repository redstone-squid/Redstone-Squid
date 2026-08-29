"""Rewrite legacy localization calls without reformatting their source files."""

import argparse
import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Replacement:
    start: int
    end: int
    text: str


def _offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _character_column(line: str, byte_column: int) -> int:
    return len(line.encode("utf-8")[:byte_column].decode("utf-8"))


def _span(node: ast.AST, source: str, offsets: list[int]) -> tuple[int, int]:
    if not hasattr(node, "end_lineno") or node.end_lineno is None or node.end_col_offset is None:
        raise ValueError("AST node has no source span")
    lines = source.splitlines(keepends=True)
    start_column = _character_column(lines[node.lineno - 1], node.col_offset)
    end_column = _character_column(lines[node.end_lineno - 1], node.end_col_offset)
    return offsets[node.lineno - 1] + start_column, offsets[node.end_lineno - 1] + end_column


def _name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def _marked_literal(node: ast.AST) -> ast.Constant | None:
    if not isinstance(node, ast.Call) or _name(node) != "_" or len(node.args) != 1 or node.keywords:
        return None
    value = node.args[0]
    return value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def _template_source(source: str) -> str:
    offsets = _offsets(source)
    starts = [
        offsets[token.start[0] - 1] + token.start[1]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.STRING
    ]
    updated = source
    for start in reversed(starts):
        updated = updated[:start] + "t" + updated[start:]
    return updated


class CallRewriter(ast.NodeVisitor):
    def __init__(self, source: str, path: Path) -> None:
        self.source = source
        self.path = path
        self.offsets = _offsets(source)
        self.replacements: list[Replacement] = []
        self.ambiguous: list[tuple[int, str]] = []
        self.parents: list[ast.AST] = []

    def visit(self, node: ast.AST) -> None:
        self.parents.append(node)
        try:
            super().visit(node)
        finally:
            self.parents.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = _name(node)
        if name == "locale_str" and len(node.args) == 1 and not node.keywords:
            deferred = node.args[0]
            if isinstance(deferred, ast.Call) and _name(deferred) == "tr" and len(deferred.args) == 1:
                template_source = ast.get_source_segment(self.source, deferred.args[0])
                if template_source is not None and template_source.startswith("t"):
                    start, end = _span(deferred, self.source, self.offsets)
                    self.replacements.append(Replacement(start, end, template_source[1:]))
                    return
        if name == "t" and len(node.args) >= 2:
            literal = _marked_literal(node.args[1])
            if literal is not None:
                literal_source = ast.get_source_segment(self.source, literal)
                if literal_source is None:
                    raise ValueError(f"cannot read literal at {self.path}:{node.lineno}")
                arguments = [literal_source]
                arguments.extend(ast.get_source_segment(self.source, argument) or "" for argument in node.args[2:])
                arguments.extend(
                    f"{keyword.arg}={ast.get_source_segment(self.source, keyword.value)}"
                    if keyword.arg is not None
                    else f"**{ast.get_source_segment(self.source, keyword.value)}"
                    for keyword in node.keywords
                )
                start, end = _span(node, self.source, self.offsets)
                self.replacements.append(Replacement(start, end, f"tr({', '.join(arguments)})"))
                return

        if name == "_" and len(node.args) == 1 and not node.keywords:
            literal = _marked_literal(node)
            if literal is None:
                self.ambiguous.append((node.lineno, "marker argument is not one string literal"))
                return
            literal_source = ast.get_source_segment(self.source, literal)
            if literal_source is None:
                raise ValueError(f"cannot read literal at {self.path}:{node.lineno}")
            parent = self.parents[-2] if len(self.parents) > 1 else None
            if isinstance(parent, ast.Call) and _name(parent) == "locale_str":
                replacement = literal_source
            elif "{" not in literal.value:
                replacement = f"tr({_template_source(literal_source)})"
            else:
                self.ambiguous.append((node.lineno, "parameterized or concatenated marker needs review"))
                return
            start, end = _span(node, self.source, self.offsets)
            self.replacements.append(Replacement(start, end, replacement))
            return

        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = list(node.names)
        changed = False
        if node.module == "squid.core.i18n" and any(alias.name == "_" for alias in names):
            names = [alias for alias in names if alias.name != "_"]
            if not any(alias.name == "tr" for alias in names):
                names.append(ast.alias(name="tr"))
            changed = True
        if node.module == "squid.bot.i18n" and any(alias.name == "t" for alias in names):
            names = [alias for alias in names if alias.name != "t"]
            changed = True
        if not changed:
            return
        imported = ", ".join(f"{alias.name} as {alias.asname}" if alias.asname else alias.name for alias in names)
        replacement = f"from {node.module} import {imported}" if imported else ""
        start, end = _span(node, self.source, self.offsets)
        self.replacements.append(Replacement(start, end, replacement))


def rewrite(path: Path, *, write: bool) -> tuple[int, list[tuple[int, str]]]:
    source = path.read_text(encoding="utf-8")
    rewriter = CallRewriter(source, path)
    rewriter.visit(ast.parse(source, filename=str(path)))
    if rewriter.replacements and write:
        updated = source
        for replacement in sorted(rewriter.replacements, key=lambda item: item.start, reverse=True):
            updated = updated[: replacement.start] + replacement.text + updated[replacement.end :]
        path.write_text(updated, encoding="utf-8", newline="")
    return len(rewriter.replacements), rewriter.ambiguous


def _python_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        files.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("squid")])
    parser.add_argument("--write", action="store_true", help="apply replacements in place")
    args = parser.parse_args()

    changed = 0
    ambiguous = 0
    for path in _python_files(args.paths):
        count, findings = rewrite(path, write=args.write)
        if count:
            changed += count
            print(f"{path}: {count} replacement(s)")
        for line, reason in findings:
            ambiguous += 1
            print(f"{path}:{line}: {reason}")
    print(f"{changed} safe replacement(s), {ambiguous} finding(s) requiring review")
    return 1 if ambiguous else 0


if __name__ == "__main__":
    sys.exit(main())
