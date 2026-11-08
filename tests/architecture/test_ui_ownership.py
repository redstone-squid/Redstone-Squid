"""Pin application UI ownership to Squid screens, widgets, and portable events."""

import ast
import re
from pathlib import Path

from tests.support.source_tree import source_tree

BOT_ROOT = Path(__file__).parents[2] / "squid" / "bot"


def _classes(tree: ast.AST) -> list[ast.ClassDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def _component_class(node: ast.ClassDef) -> bool:
    return any(
        ast.unparse(base).endswith((".Component", ".Screen"))
        or ".Component[" in ast.unparse(base)
        or ".Screen[" in ast.unparse(base)
        for base in node.bases
    )


def test_application_code_defines_no_raw_discord_ui_subclasses_or_items() -> None:
    violations: list[str] = []
    for path in BOT_ROOT.rglob("*.py"):
        tree = source_tree(path)
        for node in _classes(tree):
            for base in node.bases:
                name = ast.unparse(base)
                if name.startswith("discord.ui."):
                    violations.append(f"{path}:{node.lineno}: raw interactive base {name}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "RawItem":
                violations.append(f"{path}:{node.lineno}: RawItem")
            if isinstance(node, ast.Attribute) and node.attr == "RawItem":
                violations.append(f"{path}:{node.lineno}: {ast.unparse(node)}")

    assert not violations, "\n".join(violations)


def test_application_components_do_not_own_message_roots_or_send_methods() -> None:
    violations: list[str] = []
    for path in BOT_ROOT.rglob("*.py"):
        tree = source_tree(path)
        for component in filter(_component_class, _classes(tree)):
            for member in component.body:
                if isinstance(member, ast.AnnAssign) and "MessageRoot" in ast.unparse(member.annotation):
                    violations.append(f"{path}:{member.lineno}: {component.name} stores a MessageRoot")
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name == "send":
                    violations.append(f"{path}:{member.lineno}: {component.name}.send")

    assert not violations, "\n".join(violations)


def test_retired_event_helpers_stay_out_of_application_code() -> None:
    found: set[tuple[str, str]] = set()
    for path in BOT_ROOT.rglob("*.py"):
        relative = str(path.relative_to(BOT_ROOT))
        tree = source_tree(path)
        parents: dict[ast.AST, ast.AST] = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sd"
                and node.func.attr in {"native", "responder"}
            ):
                continue
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents.get(owner)
            function = owner.name if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"
            found.add((relative, function))

    assert not found, sorted(found)


_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*(?:![rsa])?(?::[^{}]*)?\}")
"""A `{name}`-style field, which only a template string fills in."""


def test_localized_ui_literals_interpolate_only_through_template_strings() -> None:
    """A plain-string tr() keeps braces literal, so a placeholder there is never filled in."""
    violations: list[str] = []
    for path in BOT_ROOT.rglob("*.py"):
        tree = source_tree(path)
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Name)
                or node.func.id != "tr"
                or not node.args
            ):
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and _PLACEHOLDER.search(first.value):
                violations.append(
                    f'{path}:{node.lineno}: use tr(t"…") to interpolate; plain-string braces stay literal'
                )
            if isinstance(first, ast.TemplateStr):
                violations.extend(
                    f"{path}:{node.lineno}: assign {interpolation.str!r} to an identifier before interpolation"
                    for interpolation in first.values
                    if isinstance(interpolation, ast.Interpolation) and not interpolation.str.isidentifier()
                )

    assert not violations, "\n".join(violations)
